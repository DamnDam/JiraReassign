from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    site: str = Field(
        ...,
        description="Jira Cloud base URL, e.g., https://your-domain.atlassian.net",
    )
    email: str = Field(
        ...,
        description="Jira user email associated with the API token",
    )
    api_token: SecretStr = Field(
        ...,
        description="Jira API token for the email user",
    )
    concurrency: int = Field(
        10,
        description="Number of concurrent API requests",
        ge=1,
        le=20,
    )

    model_config = SettingsConfigDict(
        env_prefix="JIRA_",
        env_file=".env",
    )
