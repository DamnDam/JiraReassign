from typing import Optional
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Combined runtime configuration for Jira and Confluence."""

    base_url: str = Field(
        ...,
        description="Atlassian Site base URL, e.g., https://your-domain.atlassian.net",
        alias="JTOOL_BASE_URL",
    )
    email: str = Field(
        ...,
        description="Atlassian user email associated with the API token",
        alias="JTOOL_EMAIL",
    )
    api_token: SecretStr = Field(
        ...,
        description="Atlassian API token for the email user",
        alias="JTOOL_API_TOKEN",
    )
    concurrency: int = Field(
        10,
        description="Number of concurrent API requests",
        ge=1,
        le=20,
        alias="JTOOL_CONCURRENCY",
    )

    def __init__(self, _env_file: Optional[str] = None, **kwargs):
        if _env_file is not None:
            super().__init__(_env_file=_env_file, **kwargs)
        else:
            super().__init__(**kwargs)

    model_config = SettingsConfigDict(
        env_prefix="JTOOL_",
        env_file=".env",
    )
