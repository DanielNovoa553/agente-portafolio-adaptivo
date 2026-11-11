import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.agent.state import OutputType
from app.schemas.evidence import Evidence
from app.schemas.interview import (
    AnswerEvaluation,
    InterviewPlanInternal,
    SuggestedAnswer,
    Verdict,
)


def utcnow() -> datetime:
    """Devuelve la fecha y hora actual en UTC con zona horaria.

    Returns:
        `datetime` consciente de zona horaria en UTC.
    """
    return datetime.now(timezone.utc)


@dataclass
class AnswerRecord:
    """Resultado registrado de una pregunta respondida.

    Attributes:
        evaluation: Evaluación estructurada devuelta por el modelo.
        score: Puntuación calculada de 0 a 100.
        verdict: Veredicto cualitativo derivado de la puntuación.
        answered_at: Momento UTC en que se registró la respuesta.
    """
    evaluation: AnswerEvaluation
    score: int
    verdict: Verdict
    answered_at: datetime


@dataclass
class InterviewSession:
    """Datos en memoria de una sesión de entrevista.

    Attributes:
        id: Identificador único generado con `uuid4`.
        created_at: Momento UTC de creación de la sesión.
        expires_at: Momento UTC en que la sesión deja de ser válida.
        job_text: Texto completo de la oferta.
        output_type: Tipo de documento generado que se usa como contexto.
        document: Documento generado serializado como diccionario.
        evidence: Evidencias seleccionadas para la sesión.
        plan: Plan interno con preguntas y puntos esperados.
        llm: Modelo de lenguaje fijado para toda la sesión.
        model_id: Identificador del modelo elegido, o `None` para el modelo por defecto.
        answers: Respuestas registradas, indexadas por ID de pregunta.
        suggestions: Borradores cacheados, indexados por ID de pregunta.
    """
    id: str
    created_at: datetime
    expires_at: datetime
    job_text: str
    output_type: OutputType
    document: dict[str, Any]
    evidence: list[Evidence]
    plan: InterviewPlanInternal
    llm: Any  # instancia real o fake; nunca se serializa
    model_id: str | None
    answers: dict[str, AnswerRecord] = field(default_factory=dict)
    suggestions: dict[str, SuggestedAnswer] = field(default_factory=dict)


class SessionNotFound(Exception):
    """La sesión no existe o ha expirado."""


class AlreadyAnswered(Exception):
    """La pregunta ya tiene una respuesta registrada."""


class InterviewSessionStore:
    """Sesiones de entrevista en memoria, con TTL y máximo de sesiones.

    Attributes:
        ttl: Duración máxima de una sesión desde su creación.
        max_sessions: Número máximo de sesiones; al superarlo se expulsa la
            más antigua.
    """

    def __init__(
        self,
        ttl_minutes: int = 120,
        max_sessions: int = 100,
        clock: Callable[[], datetime] = utcnow,
    ):
        """Inicializa el almacén con su política de caducidad y capacidad.

        Args:
            ttl_minutes: Minutos que una sesión permanece válida.
            max_sessions: Número máximo de sesiones simultáneas.
            clock: Función que devuelve la hora actual; se puede inyectar en
                pruebas para controlar el TTL.
        """
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = threading.Lock()

    def _purge_locked(self, now: datetime) -> None:
        """Elimina las sesiones caducadas; requiere el lock ya adquirido.

        Args:
            now: Momento actual usado para comparar con `expires_at`.
        """
        expired = [sid for sid, s in self._sessions.items() if now >= s.expires_at]
        for sid in expired:
            del self._sessions[sid]

    def _get_locked(self, session_id: str, now: datetime) -> InterviewSession:
        """Obtiene una sesión válida; requiere el lock ya adquirido.

        Args:
            session_id: Identificador de la sesión.
            now: Momento actual usado para comprobar la caducidad.

        Returns:
            Sesión existente y no caducada.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
        """
        session = self._sessions.get(session_id)
        if session is None or now >= session.expires_at:
            self._sessions.pop(session_id, None)
            raise SessionNotFound
        return session

    def create(
        self,
        *,
        job_text: str,
        output_type: OutputType,
        document: dict[str, Any],
        evidence: list[Evidence],
        plan: InterviewPlanInternal,
        llm: Any,
        model_id: str | None,
    ) -> InterviewSession:
        """Crea, registra y devuelve una nueva sesión de entrevista.

        Purga las sesiones caducadas y, si se alcanza `max_sessions`, expulsa
        la sesión más antigua antes de insertar la nueva.

        Args:
            job_text: Texto completo de la oferta.
            output_type: Tipo de documento generado que se usa como contexto.
            document: Documento generado serializado como diccionario.
            evidence: Evidencias seleccionadas para la sesión.
            plan: Plan interno con preguntas y puntos esperados.
            llm: Modelo de lenguaje fijado para toda la sesión.
            model_id: Identificador del modelo elegido, o `None` para usar el
                modelo por defecto.

        Returns:
            Sesión creada con ID y fechas de creación y caducidad.
        """
        now = self._clock()
        session = InterviewSession(
            id=uuid.uuid4().hex,
            created_at=now,
            expires_at=now + self.ttl,
            job_text=job_text,
            output_type=output_type,
            document=document,
            evidence=evidence,
            plan=plan,
            llm=llm,
            model_id=model_id,
        )
        with self._lock:
            self._purge_locked(now)
            while len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.created_at)
                del self._sessions[oldest.id]
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> InterviewSession:
        """Recupera una sesión válida del almacén.

        Args:
            session_id: Identificador de la sesión.

        Returns:
            Sesión existente y no caducada.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
        """
        now = self._clock()
        with self._lock:
            return self._get_locked(session_id, now)

    def exists(self, session_id: str) -> bool:
        """Indica si una sesión existe y sigue vigente.

        Args:
            session_id: Identificador de la sesión.

        Returns:
            `True` si la sesión se puede recuperar; `False` en caso contrario.
        """
        try:
            self.get(session_id)
        except SessionNotFound:
            return False
        return True

    def record_answer(
        self, session_id: str, question_id: str, record: AnswerRecord
    ) -> InterviewSession:
        """Registra la respuesta evaluada de una pregunta.

        Args:
            session_id: Identificador de la sesión.
            question_id: Identificador de la pregunta respondida.
            record: Evaluación, nota, veredicto y momento de la respuesta.

        Returns:
            Sesión actualizada.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
            AlreadyAnswered: Si la pregunta ya tiene una respuesta registrada.
        """
        now = self._clock()
        with self._lock:
            session = self._get_locked(session_id, now)
            if question_id in session.answers:
                raise AlreadyAnswered
            session.answers[question_id] = record
            return session

    def snapshot_answers(self, session_id: str) -> dict[str, AnswerRecord]:
        """Devuelve una copia de las respuestas registradas en la sesión.

        Args:
            session_id: Identificador de la sesión.

        Returns:
            Diccionario de respuestas indexado por ID de pregunta.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
        """
        now = self._clock()
        with self._lock:
            session = self._get_locked(session_id, now)
            return dict(session.answers)

    def get_suggestion(
        self, session_id: str, question_id: str
    ) -> SuggestedAnswer | None:
        """Recupera el borrador cacheado de una pregunta, si existe.

        Args:
            session_id: Identificador de la sesión.
            question_id: Identificador de la pregunta.

        Returns:
            Borrador cacheado, o `None` si aún no se ha generado.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
        """
        now = self._clock()
        with self._lock:
            session = self._get_locked(session_id, now)
            return session.suggestions.get(question_id)

    def record_suggestion(
        self, session_id: str, question_id: str, suggestion: SuggestedAnswer
    ) -> InterviewSession:
        """Guarda el borrador de respuesta de una pregunta pendiente.

        Args:
            session_id: Identificador de la sesión.
            question_id: Identificador de la pregunta.
            suggestion: Borrador validado que se quiere cachear.

        Returns:
            Sesión actualizada.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
            AlreadyAnswered: Si la pregunta ya tiene una respuesta registrada.
        """
        now = self._clock()
        with self._lock:
            session = self._get_locked(session_id, now)
            if question_id in session.answers:
                raise AlreadyAnswered
            session.suggestions[question_id] = suggestion
            return session

    def delete(self, session_id: str) -> bool:
        """Elimina una sesión del almacén.

        Args:
            session_id: Identificador de la sesión.

        Returns:
            `True` si la sesión existía y se eliminó.

        Raises:
            SessionNotFound: Si la sesión no existe o ha expirado.
        """
        now = self._clock()
        with self._lock:
            self._get_locked(session_id, now)
            del self._sessions[session_id]
            return True

    def count(self) -> int:
        """Cuenta las sesiones vigentes tras purgar las caducadas.

        Returns:
            Número de sesiones actualmente en memoria.
        """
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            return len(self._sessions)

    def reset(self) -> None:
        """Vacía el almacén y elimina todas las sesiones."""
        with self._lock:
            self._sessions.clear()


_store: InterviewSessionStore | None = None


def get_interview_store() -> InterviewSessionStore:
    """Devuelve el almacén global de entrevistas, creándolo si hace falta.

    La configuración de TTL y capacidad se toma de `settings`.

    Returns:
        Instancia única de `InterviewSessionStore`.
    """
    global _store
    if _store is None:
        from app.core.config import settings

        _store = InterviewSessionStore(
            ttl_minutes=settings.interview_session_ttl_minutes,
            max_sessions=settings.interview_max_sessions,
        )
    return _store
