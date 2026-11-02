from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
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


settings = Settings()
