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
    threecx_test_destination: str = ""
    prerecorded_message_path: str = "/app/media/test-message.mp3"
    audio_storage_dir: str = "/app/media/uploads"
    max_audio_upload_bytes: int = 25 * 1024 * 1024
    threecx_test_call_timeout_seconds: int = 45
    threecx_test_forward_destination: str = ""
    threecx_test_first_destination: str = ""
    threecx_test_forward_wait_seconds: int = 25
    threecx_test_hold_wait_seconds: int = 45
    transfer_message_path: str = ""
    session_ttl_hours: int = 12
    transcript_sync_enabled: bool = True
    recording_sync_enabled: bool = True
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 3
    # Dinstar gateway HTTP API for live SIM status (reached privately over Tailscale).
    # Leave the URL, username or password empty to turn SIM checks off.
    dinstar_base_url: str = ""
    dinstar_api_username: str = ""
    dinstar_api_password: str = ""
    dinstar_verify_tls: bool = True
    dinstar_timeout_seconds: float = 5.0

    @property
    def dinstar_configured(self) -> bool:
        return bool(self.dinstar_base_url and self.dinstar_api_username and self.dinstar_api_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
