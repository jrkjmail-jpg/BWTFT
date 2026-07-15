from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    openai_api_key: str
    openai_text_model: str = "gpt-5.2"
    openai_vision_model: str = "gpt-5.2"
    openai_transcribe_model: str = "gpt-4o-mini-transcribe"
    database_url: str = "sqlite+aiosqlite:///./data/bwtft.sqlite3"
    admin_user_ids: str = ""
    nvidia_api_key: str = ""
    nvidia_image_endpoint: str = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev"
    nvidia_reference_image_endpoint: str = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev"
    nvidia_image_model: str = "black-forest-labs/flux.1-dev"
    nvidia_reference_image_model: str = "black-forest-labs/flux.1-kontext-dev"
    nvidia_reference_images_max: int = 1
    nvidia_image_width: int = 1024
    nvidia_image_height: int = 1024

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def allowed_user_ids(self) -> set[int]:
        ids: set[int] = set()
        for raw_id in self.admin_user_ids.replace(";", ",").split(","):
            raw_id = raw_id.strip()
            if raw_id:
                try:
                    ids.add(int(raw_id))
                except ValueError:
                    continue
        return ids


settings = Settings()
