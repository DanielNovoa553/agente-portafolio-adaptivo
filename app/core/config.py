from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación cargada desde `.env` y variables de entorno.

    Attributes:
        llm_provider: Proveedor de LLM configurado (solo Gemini está implementado).
        google_api_key: Clave de API de Google para Gemini y los embeddings.
        llm_model: Modelo de Gemini usado por defecto.
        chroma_path: Ruta del almacenamiento persistente de ChromaDB.
        embedding_model: Modelo de embeddings usado al indexar y consultar.
        log_level: Nivel de logging, por ejemplo `INFO`.
        generate_rate_limit: Límite de peticiones para `/generate`.
        ingest_rate_limit: Límite de peticiones para `/ingest`.
        max_upload_bytes: Tamaño máximo permitido por archivo subido.
        interview_start_rate_limit: Límite al crear sesiones de entrevista.
        interview_answer_rate_limit: Límite al evaluar respuestas.
        interview_suggestion_rate_limit: Límite al pedir respuestas sugeridas.
        interview_session_ttl_minutes: Minutos de vida de una sesión de entrevista.
        interview_max_sessions: Número máximo de sesiones en memoria.
        interview_question_count: Número de preguntas por defecto de la entrevista.
        interview_max_answer_chars: Longitud máxima del texto de una respuesta.
        interview_max_evidence: Número máximo de evidencias enviadas al LLM.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "gemini"
    google_api_key: str = ""
    llm_model: str = "gemini-3.1-flash-lite"
    chroma_path: str = "./chroma_db"
    embedding_model: str = "gemini-embedding-001"
    log_level: str = "INFO"
    generate_rate_limit: str = "10/minute"
    ingest_rate_limit: str = "20/minute"
    max_upload_bytes: int = 5_000_000
    interview_start_rate_limit: str = "5/minute"
    interview_answer_rate_limit: str = "30/minute"
    interview_suggestion_rate_limit: str = "10/minute"
    interview_session_ttl_minutes: int = 120
    interview_max_sessions: int = 100
    interview_question_count: int = 6
    interview_max_answer_chars: int = 5000
    interview_max_evidence: int = 16


settings = Settings()
