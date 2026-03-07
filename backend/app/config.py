from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://postgres:password@localhost:5432/energypulse"
    redis_url: str = "redis://localhost:6379"
    eia_api_key: str = ""
    anthropic_api_key: str = ""
    sendgrid_api_key: str = ""
    slack_webhook_url: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
