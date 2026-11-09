import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.agent.state import OutputType
from app.schemas.evidence import Evidence
from app.schemas.interview import AnswerEvaluation, InterviewPlanInternal, Verdict


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AnswerRecord:
    evaluation: AnswerEvaluation
    score: int
    verdict: Verdict
    answered_at: datetime


@dataclass
class InterviewSession:
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


class SessionNotFound(Exception):
    """La sesión no existe o ha expirado."""


class AlreadyAnswered(Exception):
    """La pregunta ya tiene una respuesta registrada."""


class InterviewSessionStore:
    """Sesiones de entrevista en memoria, con TTL y máximo de sesiones."""

    def __init__(
        self,
        ttl_minutes: int = 120,
        max_sessions: int = 100,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, InterviewSession] = {}
        self._lock = threading.Lock()

    def _purge_locked(self, now: datetime) -> None:
        expired = [sid for sid, s in self._sessions.items() if now >= s.expires_at]
        for sid in expired:
            del self._sessions[sid]

    def _get_locked(self, session_id: str, now: datetime) -> InterviewSession:
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
        now = self._clock()
        with self._lock:
            return self._get_locked(session_id, now)

    def exists(self, session_id: str) -> bool:
        try:
            self.get(session_id)
        except SessionNotFound:
            return False
        return True

    def record_answer(
        self, session_id: str, question_id: str, record: AnswerRecord
    ) -> InterviewSession:
        now = self._clock()
        with self._lock:
            session = self._get_locked(session_id, now)
            if question_id in session.answers:
                raise AlreadyAnswered
            session.answers[question_id] = record
            return session

    def snapshot_answers(self, session_id: str) -> dict[str, AnswerRecord]:
        now = self._clock()
        with self._lock:
            session = self._get_locked(session_id, now)
            return dict(session.answers)

    def delete(self, session_id: str) -> bool:
        now = self._clock()
        with self._lock:
            self._get_locked(session_id, now)
            del self._sessions[session_id]
            return True

    def count(self) -> int:
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            return len(self._sessions)

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()


_store: InterviewSessionStore | None = None


def get_interview_store() -> InterviewSessionStore:
    global _store
    if _store is None:
        from app.core.config import settings

        _store = InterviewSessionStore(
            ttl_minutes=settings.interview_session_ttl_minutes,
            max_sessions=settings.interview_max_sessions,
        )
    return _store
