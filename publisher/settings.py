from typing import Self

from pydantic import HttpUrl, NonNegativeFloat, NonNegativeInt, PositiveFloat, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PublisherSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    CLIENT_ID: str = "default"
    ALLOWED_CATEGORIES: list[str] = ["blog", "guides", "news"]
    DATABASE_URL: PostgresDsn | None = None


class WordPressSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    WP_URL: HttpUrl
    WP_USER: str
    WP_APP_PASSWORD: SecretStr
    WP_ALLOW_HTTP: bool = False
    WP_MIN_REQUEST_INTERVAL_SECONDS: NonNegativeFloat = 1.0
    WP_MAX_RETRIES: NonNegativeInt = 4
    WP_TIMEOUT_SECONDS: PositiveFloat = 30.0
    WP_BACKOFF_BASE_SECONDS: NonNegativeFloat = 2.0

    @model_validator(mode="after")
    def require_https(self) -> Self:
        # Application Passwords travel as Basic auth, so plain HTTP leaks them.
        if self.WP_URL.scheme != "https" and not self.WP_ALLOW_HTTP:
            raise ValueError("WP_URL must use https:// (set WP_ALLOW_HTTP=true only for local mocks)")
        return self
