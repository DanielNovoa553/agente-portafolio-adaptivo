import logging

import pytest
from fastapi.testclient import TestClient

from app.api.deps import interview_store_dep, llm_dep
from app.core.limiter import limiter
from app.interview.store import InterviewSessionStore
from app.main import app
from tests.test_agent import FakeLLM
from tests.test_interview import (
    DOCUMENT,
    EVIDENCE,
    JOB,
    make_evaluation,
    make_plan,
    make_suggestion,
)


def payload(**overrides):
    """Construye el cuerpo JSON para crear una sesión de entrevista.

    Args:
        **overrides: Campos que reemplazan los valores por defecto del cuerpo.

    Returns:
        Diccionario con la oferta, el documento, las evidencias y el número de
        preguntas.
    """
    data = {
        "job_text": JOB,
        "output_type": "cv",
        "document": DOCUMENT,
        "evidence": [e.model_dump(mode="json") for e in EVIDENCE],
        "question_count": 6,
    }
    data.update(overrides)
    return data


@pytest.fixture
def istore():
    """Proporciona un almacén de sesiones de entrevista vacío por test.

    Yields:
        InterviewSessionStore con TTL y máximo de sesiones por defecto.
    """
    store = InterviewSessionStore(ttl_minutes=120, max_sessions=100)
    yield store
    store.reset()


@pytest.fixture
def make_client(istore):
    """Proporciona una fábrica de clientes con las dependencias sustituidas.

    Desactiva el rate limit mientras dura el test y restaura el estado al final.

    Args:
        istore: Almacén de sesiones que se inyecta en `interview_store_dep`.

    Yields:
        Función que recibe un LLM opcional y devuelve un TestClient con la
        cabecera de consentimiento.
    """
    limiter.enabled = False
    limiter.reset()

    def _make(llm=None):
        """Crea un cliente de pruebas con el LLM y el almacén indicados.

        Args:
            llm: Doble del LLM inyectado en `llm_dep`; puede ser None.

        Returns:
            TestClient configurado con la cabecera de consentimiento.
        """
        app.dependency_overrides[interview_store_dep] = lambda: istore
        app.dependency_overrides[llm_dep] = lambda: llm
        return TestClient(app, headers={"X-Privacy-Consent": "2"})

    yield _make
    app.dependency_overrides.clear()
    limiter.enabled = True
    limiter.reset()


def test_create_session_returns_public_questions(make_client):
    """Verifica que crear una sesión devuelve 201 con preguntas públicas.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan()]})
    r = make_client(llm).post("/interview/sessions", json=payload())

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session_id"]
    assert body["expires_at"]
    assert body["role_title"] == "Backend Engineer"
    assert len(body["questions"]) == 6
    assert [q["id"] for q in body["questions"]] == [f"q{i}" for i in range(1, 7)]
    assert "expected_points" not in body["questions"][0]


def test_invalid_document_is_422(make_client):
    """Verifica que un documento incompleto devuelve 422 sin invocar al LLM.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": []})
    r = make_client(llm).post(
        "/interview/sessions", json=payload(document={"headline": "incompleto"})
    )

    assert r.status_code == 422
    assert "documento" in r.json()["detail"]
    assert llm.calls == []


def test_full_answer_summary_and_delete_flow(make_client):
    """Verifica el flujo de responder, rechazar un duplicado, resumir y borrar.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "AnswerEvaluation": [make_evaluation()],
        }
    )
    c = make_client(llm)
    sid = c.post("/interview/sessions", json=payload()).json()["session_id"]

    r = c.post(
        f"/interview/sessions/{sid}/answers",
        json={"question_id": "q1", "answer": "Mi respuesta concreta."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["score"] == 80
    assert body["verdict"] == "strong"
    assert len(body["criteria"]) == 5
    assert body["answered"] == 1
    assert body["total"] == 6
    assert body["next_question_id"] == "q2"
    assert body["evidence"][0]["id"] == "cv.md::0"

    duplicate = c.post(
        f"/interview/sessions/{sid}/answers",
        json={"question_id": "q1", "answer": "Otra respuesta."},
    )
    assert duplicate.status_code == 409

    summary = c.get(f"/interview/sessions/{sid}/summary")
    assert summary.status_code == 200
    data = summary.json()
    assert data["answered"] == 1
    assert data["completed"] is False
    assert data["overall_score"] == 80
    assert data["unanswered_question_ids"] == ["q2", "q3", "q4", "q5", "q6"]

    assert c.delete(f"/interview/sessions/{sid}").status_code == 204
    assert c.get(f"/interview/sessions/{sid}/summary").status_code == 404


def test_answer_to_unknown_session_is_404(make_client):
    """Verifica que responder en una sesión inexistente devuelve 404.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    c = make_client(FakeLLM({}))
    r = c.post(
        "/interview/sessions/noexiste/answers",
        json={"question_id": "q1", "answer": "Respuesta."},
    )
    assert r.status_code == 404
    assert "expirada" in r.json()["detail"]


def test_unknown_question_is_422(make_client):
    """Verifica que responder a una pregunta ajena a la sesión devuelve 422.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan()]})
    c = make_client(llm)
    sid = c.post("/interview/sessions", json=payload()).json()["session_id"]

    r = c.post(
        f"/interview/sessions/{sid}/answers",
        json={"question_id": "q99", "answer": "Respuesta."},
    )
    assert r.status_code == 422
    assert "no pertenece" in r.json()["detail"]


def test_empty_answer_is_422(make_client):
    """Verifica que una respuesta vacía o con solo espacios devuelve 422.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan()]})
    c = make_client(llm)
    sid = c.post("/interview/sessions", json=payload()).json()["session_id"]

    r = c.post(
        f"/interview/sessions/{sid}/answers",
        json={"question_id": "q1", "answer": "   "},
    )
    assert r.status_code == 422
    assert "vacía" in r.json()["detail"]


def test_too_long_answer_is_422_without_echo(make_client, monkeypatch):
    """Verifica que una respuesta demasiado larga devuelve 422 sin repetir su texto.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
        monkeypatch: Fixture que reduce el máximo de caracteres permitidos.
    """
    monkeypatch.setattr("app.api.interview.settings.interview_max_answer_chars", 10)
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan()]})
    c = make_client(llm)
    sid = c.post("/interview/sessions", json=payload()).json()["session_id"]

    secret = "SECRETO" * 20
    r = c.post(
        f"/interview/sessions/{sid}/answers",
        json={"question_id": "q1", "answer": secret},
    )
    assert r.status_code == 422
    assert "SECRETO" not in r.text
    assert "10 caracteres" in r.json()["detail"]


def test_summary_without_answers_is_422(make_client):
    """Verifica que pedir el resumen sin respuestas registradas devuelve 422.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan()]})
    c = make_client(llm)
    sid = c.post("/interview/sessions", json=payload()).json()["session_id"]

    r = c.get(f"/interview/sessions/{sid}/summary")
    assert r.status_code == 422
    assert "al menos una" in r.json()["detail"]


def test_delete_unknown_session_is_404(make_client):
    """Verifica que borrar una sesión inexistente devuelve 404.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    assert make_client(FakeLLM({})).delete("/interview/sessions/noexiste").status_code == 404


def test_interview_routes_require_consent(make_client):
    """Verifica que las rutas de entrevista devuelven 403 sin consentimiento.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    c = make_client(FakeLLM({}))
    c.headers.pop("X-Privacy-Consent")

    assert c.post("/interview/sessions", json=payload()).status_code == 403
    assert (
        c.post(
            "/interview/sessions/x/answers",
            json={"question_id": "q1", "answer": "Respuesta."},
        ).status_code
        == 403
    )
    assert c.get("/interview/sessions/x/summary").status_code == 403
    assert c.delete("/interview/sessions/x").status_code == 403
    assert c.post("/interview/sessions/x/questions/q1/suggestion").status_code == 403


def test_interview_rejects_unknown_model(istore, monkeypatch):
    """Verifica que crear una sesión con un modelo no permitido devuelve 422.

    Args:
        istore: Almacén de sesiones inyectado en la aplicación.
        monkeypatch: Fixture que fuerza a rechazar cualquier modelo.
    """
    limiter.enabled = False
    app.dependency_overrides[interview_store_dep] = lambda: istore
    monkeypatch.setattr("app.api.deps.is_allowed", lambda m: False)
    try:
        r = TestClient(app, headers={"X-Privacy-Consent": "2"}).post(
            "/interview/sessions?model=inventado", json=payload()
        )
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = True
    assert r.status_code == 422


def test_interview_missing_api_key_is_503(istore, monkeypatch):
    """Verifica que crear una sesión sin API key de Gemini devuelve 503.

    Args:
        istore: Almacén de sesiones inyectado en la aplicación.
        monkeypatch: Fixture que vacía la API key configurada.
    """
    limiter.enabled = False
    app.dependency_overrides[interview_store_dep] = lambda: istore
    app.dependency_overrides.pop(llm_dep, None)
    monkeypatch.setattr("app.agent.llm.settings.google_api_key", "")
    try:
        r = TestClient(app, headers={"X-Privacy-Consent": "2"}).post(
            "/interview/sessions", json=payload()
        )
    finally:
        app.dependency_overrides.clear()
        limiter.enabled = True
    assert r.status_code == 503


def test_interview_start_rate_limit_returns_429(make_client):
    """Verifica que la sexta creación de sesión en un minuto devuelve 429.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan() for _ in range(5)]})
    c = make_client(llm)
    limiter.enabled = True
    limiter.reset()

    codes = [c.post("/interview/sessions", json=payload()).status_code for _ in range(6)]
    assert codes[:5] == [201] * 5
    assert codes[-1] == 429


def create_session(c, llm=None):
    """Crea una sesión de entrevista y devuelve su identificador.

    Args:
        c: Cliente HTTP de pruebas.
        llm: Parámetro opcional no utilizado por el helper.

    Returns:
        Identificador de la sesión creada.
    """
    return c.post("/interview/sessions", json=payload()).json()["session_id"]


def test_suggestion_returns_grounded_draft(make_client):
    """Verifica que la sugerencia incluye borrador, esquema, avisos y evidencias.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "SuggestedAnswer": [make_suggestion()],
        }
    )
    c = make_client(llm)
    sid = create_session(c)

    r = c.post(f"/interview/sessions/{sid}/questions/q1/suggestion")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["question_id"] == "q1"
    assert body["answer"].startswith("En mi experiencia")
    assert body["outline"] == ["Contexto", "Decisión", "Resultado"]
    assert body["caveats"]
    assert body["preparation_notes"]
    assert [e["id"] for e in body["evidence"]] == ["cv.md::0"]


def test_suggestion_is_cached(make_client):
    """Verifica que dos peticiones reutilizan la caché sin llamar de nuevo al LLM.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "SuggestedAnswer": [make_suggestion()],
        }
    )
    c = make_client(llm)
    sid = create_session(c)

    first = c.post(f"/interview/sessions/{sid}/questions/q1/suggestion")
    second = c.post(f"/interview/sessions/{sid}/questions/q1/suggestion")

    assert first.status_code == 200
    assert first.json() == second.json()
    assert len(llm.calls) == 2  # plan + una sola generación


def test_suggestion_unknown_session_is_404(make_client):
    """Verifica que pedir sugerencia para una sesión inexistente devuelve 404.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    r = make_client(FakeLLM({})).post(
        "/interview/sessions/noexiste/questions/q1/suggestion"
    )
    assert r.status_code == 404
    assert "expirada" in r.json()["detail"]


def test_suggestion_answered_question_is_409(make_client):
    """Verifica que pedir sugerencia para una pregunta ya respondida devuelve 409.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "AnswerEvaluation": [make_evaluation()],
        }
    )
    c = make_client(llm)
    sid = create_session(c)
    assert (
        c.post(
            f"/interview/sessions/{sid}/answers",
            json={"question_id": "q1", "answer": "Mi respuesta."},
        ).status_code
        == 200
    )

    r = c.post(f"/interview/sessions/{sid}/questions/q1/suggestion")

    assert r.status_code == 409
    assert "Ya respondiste" in r.json()["detail"]


def test_suggestion_unknown_question_is_422(make_client):
    """Verifica que pedir sugerencia para una pregunta ajena a la sesión da 422.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM({"GeneratedInterviewPlan": [make_plan()]})
    c = make_client(llm)
    sid = create_session(c)

    r = c.post(f"/interview/sessions/{sid}/questions/q99/suggestion")

    assert r.status_code == 422
    assert "no pertenece" in r.json()["detail"]


def test_suggestion_failure_is_502(make_client):
    """Verifica que un borrador con evidencia inventada agota reintentos y da 502.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    bad = make_suggestion(evidence_ids=["inventado::9"])
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "SuggestedAnswer": [bad, bad],
        }
    )
    c = make_client(llm)
    sid = create_session(c)

    r = c.post(f"/interview/sessions/{sid}/questions/q1/suggestion")

    assert r.status_code == 502
    assert "borrador" in r.json()["detail"]


def test_suggestion_rate_limit_returns_429(make_client):
    """Verifica que la undécima sugerencia en un minuto devuelve 429.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
    """
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "SuggestedAnswer": [make_suggestion()],
        }
    )
    c = make_client(llm)
    sid = create_session(c)
    limiter.enabled = True
    limiter.reset()

    codes = [
        c.post(f"/interview/sessions/{sid}/questions/q1/suggestion").status_code
        for _ in range(11)
    ]
    assert codes[:10] == [200] * 10
    assert codes[-1] == 429


def test_suggestion_text_is_not_logged(make_client, caplog):
    """Verifica que el texto del borrador no se escribe en los logs.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
        caplog: Capturador de logs del test.
    """
    secret = "BORRADOR-SECRETO-QUE-NO-DEBE-LOGUEARSE"
    suggestion = make_suggestion(
        answer=(
            f"{secret}. En mi experiencia construí APIs con FastAPI y procesé "
            "documentos con AWS Lambda y SQS, midiendo la antigüedad de la cola."
        )
    )
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "SuggestedAnswer": [suggestion],
        }
    )
    c = make_client(llm)
    sid = create_session(c)

    with caplog.at_level(logging.DEBUG):
        r = c.post(f"/interview/sessions/{sid}/questions/q1/suggestion")

    assert r.status_code == 200
    assert secret not in caplog.text


def test_answer_text_is_not_logged(make_client, caplog):
    """Verifica que el texto de la respuesta no aparece en logs ni en la respuesta.

    Args:
        make_client: Fábrica de clientes con dependencias sustituidas.
        caplog: Capturador de logs del test.
    """
    secret = "TEXTO-SECRETO-QUE-NO-DEBE-LOGUEARSE"
    llm = FakeLLM(
        {
            "GeneratedInterviewPlan": [make_plan()],
            "AnswerEvaluation": [make_evaluation()],
        }
    )
    c = make_client(llm)
    sid = c.post("/interview/sessions", json=payload()).json()["session_id"]

    with caplog.at_level(logging.DEBUG):
        r = c.post(
            f"/interview/sessions/{sid}/answers",
            json={"question_id": "q1", "answer": secret},
        )

    assert r.status_code == 200
    assert secret not in caplog.text
    assert secret not in r.text
