from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./jamal_dialler.db"
    call_provider: str = "simulator"
    max_concurrent_calls: int = 8
    cors_origins: str = "http://localhost:8000"
    threecx_base_url: str = ""
    threecx_app_id: str = ""
    threecx_api_key: str = ""
    threecx_control_extension: str = ""
    threecx_timeout_seconds: float = 15.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
