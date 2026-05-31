from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    openai_api_key: str
    openai_text_model: str = "gpt-5.2"
    openai_vision_model: str = "gpt-5.2"
    openai_transcribe_model: str = "gpt-4o-mini-transcribe"
    database_url: str = "sqlite+aiosqlite:///./data/bwtft.sqlite3"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
