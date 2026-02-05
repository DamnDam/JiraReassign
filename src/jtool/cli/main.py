from typing import Optional, cast
import logging
import asyncio

import typer
from pydantic import ValidationError

from jtool.term import Console
from jtool.config import Settings
from jtool.client.base import APIError
from jtool.client.jira import JiraClient

from .base import CLIContext, logger
from .remap import app as remap

app = typer.Typer(
    help="Bulk replace users in Jira Cloud by reassigning issue assignees.",
)

app.add_typer(remap, name="remap")


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    env_file: Optional[str] = typer.Option(
        None, "--env-file", help="Path to .env file to load environment variables from."
    ),
):
    """Jira Reassign CLI Tool."""
    console = Console()
    console.add_logger(logger)
    console.add_logger(logging.getLogger("jtool.client"))
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(1)

    try:
        settings = Settings(env_file=env_file)
    except ValidationError as e:
        logger.error(
            "Environment not configured correctly. Please export appropriate environment variables:\n"
            + "\n".join(f"- {err['loc'][0]}: {err['msg']}" for err in e.errors()),
        )
        raise typer.Exit(2)

    ctx.obj = CLIContext(console=console, settings=settings)


@app.command("check")
def check_connection(
    ctx: typer.Context,
):
    """Check connection to Jira with provided settings."""
    conf = cast(CLIContext, ctx.obj)
    settings = conf.settings
    console = conf.console

    async def main():
        async with settings.get_client(JiraClient) as client:
            try:
                user = await client.get_self()
                console.print(
                    f"Connected to Jira site '{settings.base_url}' as user '{user.displayName}' ({user.emailAddress}) - {user.accountId}"
                )
            except APIError as e:
                logger.error(
                    f"Failed to connect to Jira: {str(e)}",
                )
                raise typer.Exit(10)

    asyncio.run(main())


@app.command("find")
def find_users(
    ctx: typer.Context,
    identifiers: str = typer.Argument(
        ...,
        help="Comma-separated list of user identifiers (email or accountId) to find.",
    ),
):
    """Find and display user information for given identifiers."""
    conf = cast(CLIContext, ctx.obj)
    settings = conf.settings
    console = conf.console

    async def main():
        async with settings.get_client(JiraClient) as client:
            ids = [iden.strip() for iden in identifiers.split(",")]
            for iden in ids:
                try:
                    user = await client.resolve_user(iden)
                except APIError as e:
                    logger.warning(
                        f"Error resolving identifier '{iden}': {str(e)}",
                    )
                else:
                    console.print(
                        f"Identifier '{iden}' resolved to User: {user.displayName} ({user.emailAddress}), AccountId: '{user.accountId}'"
                    )

    asyncio.run(main())
