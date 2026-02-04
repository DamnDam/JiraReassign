from typing import Optional, TypeVar
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .client.base import BaseClient

ClientType = TypeVar("ClientType", bound=BaseClient)


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

    def get_client(self, client_type: type[ClientType]) -> ClientType:
        """Instantiate a client of the specified type using the current settings."""
        return client_type(
            base_url=self.base_url,
            concurrency=self.concurrency,
            auth=(self.email, self.api_token.get_secret_value()),
        )

    def __init__(self, env_file: Optional[str] = None):
        if env_file is not None:
            super().__init__(_env_file=env_file)
        else:
            super().__init__()

    model_config = SettingsConfigDict(
        env_prefix="JTOOL_",
        env_file=".env",
    )
