from typing import Optional
import asyncio
import csv
from dataclasses import dataclass
from itertools import chain

import typer
from pydantic import ValidationError

from rich.table import Table

from .term import DualConsole, Progress, create_progress
from .config import Settings
from .jira_client import JiraClient, JiraApiError, User, Task


@dataclass
class CLIContext:
    """Context for CLI commands."""

    console: DualConsole
    settings: Settings

    @property
    def progress(self) -> Progress:
        """Create a Rich Progress instance."""
        if not hasattr(self, "_progress"):
            self._progress = create_progress(self.console)
        return self._progress


@dataclass
class RemapContext(CLIContext):
    """Context for remap commands."""

    user_maps: list[tuple[User, User]]


app = typer.Typer(
    help="Bulk replace users in Jira Cloud by reassigning issue assignees.",
)
remap = typer.Typer(
    help="Reassign from old users to new users based on a CSV mapping.",
    no_args_is_help=True,
)
app.add_typer(remap, name="remap")


def get_jira_client(conf: CLIContext) -> JiraClient:
    """Helper to create a JiraClient from CLIContext.
    Should be used in an async with block.
    """
    assert isinstance(conf, CLIContext)
    return JiraClient(
        console=conf.console,
        base_url=conf.settings.site,
        concurrency=conf.settings.concurrency,
        auth=(conf.settings.email, conf.settings.api_token.get_secret_value()),
    )


@app.callback(invoke_without_command=True)
def init(
    ctx: typer.Context,
    env_file: Optional[str] = typer.Option(
        None, "--env-file", help="Path to .env file to load environment variables from."
    ),
):
    """Jira Reassign CLI Tool."""
    console = DualConsole()
    if ctx.invoked_subcommand is None:
        console.log_error(ctx.get_help())
        raise typer.Exit(1)

    try:
        settings = Settings(_env_file=env_file) if env_file else Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as e:
        console.log_error(
            "Environment not set. Please export JIRA_SITE, JIRA_EMAIL, JIRA_API_TOKEN.\n"
            + str(e)
        )
        raise typer.Exit(code=2)

    ctx.obj = CLIContext(console=console, settings=settings)


@app.command("check")
def check_connection(
    ctx: typer.Context,
):
    """Check connection to Jira with provided settings."""
    conf: CLIContext = ctx.obj
    assert isinstance(ctx.obj, CLIContext)
    settings = conf.settings
    console = conf.console

    async def main():
        async with get_jira_client(conf) as client:
            try:
                user = await client.get_self()
                console.print(
                    f"Connected to Jira site '{settings.site}' as user '{user.displayName}' ({user.emailAddress})."
                )
            except JiraApiError as e:
                console.log_error(
                    f"Failed to connect to Jira: {e.status_code} {e.reason}",
                )
                raise typer.Exit(code=1)

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
    conf: CLIContext = ctx.obj
    assert isinstance(conf, CLIContext)
    console = conf.console

    async def main():
        async with get_jira_client(conf) as client:
            ids = [iden.strip() for iden in identifiers.split(",")]
            for iden in ids:
                user = await client.resolve_user(iden)
                if user:
                    console.print(
                        f"Identifier '{iden}' resolved to User: {user.displayName} ({user.emailAddress}), AccountId: '{user.accountId}'"
                    )
                else:
                    console.log_error(
                        f"Identifier '{iden}' could not be resolved to a user.",
                    )

    asyncio.run(main())


@remap.callback(invoke_without_command=True)
def remap_callback(
    ctx: typer.Context,
    mapping_csv: str = typer.Argument(
        ...,
        help="CSV file with headers 'old,new' mapping identifiers (email or accountId).",
    ),
    concurrency: Optional[int] = typer.Option(
        None, "--concurrency", help="Number of concurrent requests."
    ),
):
    """Initialize remappping."""
    conf: CLIContext = ctx.obj
    assert isinstance(conf, CLIContext)
    console = conf.console
    progress = conf.progress
    settings = conf.settings
    progress = conf.progress

    if ctx.invoked_subcommand is None:
        console.log_error(ctx.get_help())
        raise typer.Exit(1)

    if concurrency:
        try:
            settings.concurrency = concurrency
        except ValidationError as e:
            console.log_error(f"Invalid concurrency value: {e}")
            raise typer.Exit(code=2)

    with open(mapping_csv, newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames is not None
        if "old" not in reader.fieldnames or "new" not in reader.fieldnames:
            console.log_error("CSV must have headers 'old' and 'new'.")
            raise typer.Exit(code=2)
        rows = list(reader)

    async def main(rows: list[dict[str, str]]):
        progress.add_task(
            description=f"Resolving users ({len(rows)})...", total=len(rows)
        )

        async with get_jira_client(conf) as client:

            async def resolve_user_map(
                row: dict[str, str],
            ) -> Optional[tuple[User, User]]:
                old_user, new_user = await asyncio.gather(
                    client.resolve_user(row["old"].strip()),
                    client.resolve_user(row["new"].strip()),
                )
                progress.update(progress.tasks[-1].id, advance=1)
                if not old_user:
                    console.log_error(f"Old user '{row['old']}' not found; skipping.")
                    return None
                if not new_user:
                    console.log_error(f"New user '{row['new']}' not found; skipping.")
                    return None
                return (old_user, new_user)

            with console, progress:
                user_solved = await asyncio.gather(
                    *(resolve_user_map(row) for row in rows)
                )
            user_maps = [um for um in user_solved if um is not None]

        ctx.obj = RemapContext(
            console=console,
            settings=settings,
            user_maps=user_maps,
        )

    asyncio.run(main(rows))


@remap.command("filters")
def remap_filters(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only show counts; no changes."
    ),
):
    """Reassign filters from old -> new users according to the CSV mapping."""
    conf: RemapContext = ctx.obj
    assert isinstance(conf, RemapContext)
    console = conf.console
    progress = conf.progress

    async def main():
        total = 0
        changed = 0
        user_maps = conf.user_maps
        async with get_jira_client(conf) as client:
            with console, progress:

                async def find_filters(user: User) -> list[str]:
                    results = await client.get_filters_for_user(user)
                    progress.update(progress.tasks[-1].id, advance=1)
                    return results

                async def reassign_filter(filter_id: str, new: User) -> None:
                    nonlocal changed
                    await client.set_filter_owner(filter_id, new.accountId)
                    changed += 1
                    progress.update(progress.tasks[-1].id, advance=1)

                progress.add_task(
                    description="Gathering filters...", total=len(user_maps)
                )
                filters_results = await asyncio.gather(
                    *(find_filters(user) for user, _ in user_maps)
                )

                filter_maps = [
                    (old, new, filters)
                    for (old, new), filters in zip(
                        user_maps,
                        filters_results,
                    )
                    if filters
                ]

                progress_user = progress.add_task(
                    description=f"Remapping users ({len(filter_maps)})...",
                    total=len(filter_maps),
                    visible=not dry_run and len(filter_maps) > 0,
                )
                for old, new, filters in filter_maps:
                    user_total = len(filters)
                    total += user_total
                    if dry_run or not user_total:
                        continue

                    progress_filters = progress.add_task(
                        description=f"  {old.emailAddress or old.displayName} -> {new.emailAddress or new.displayName} ({user_total})...",
                        total=user_total,
                    )
                    await asyncio.gather(
                        *(reassign_filter(fid, new) for fid in filters)
                    )

                    progress.update(
                        progress_filters, total=user_total, completed=user_total
                    )
                    progress.update(progress_user, advance=1)

        if dry_run:
            table = Table()
            table.add_column("Old User", style="cyan", no_wrap=True)
            table.add_column("New User", style="cyan", no_wrap=True)
            table.add_column("Total Filters", style="green")
            for old, new, filters in filter_maps:
                table.add_row(
                    f"{old.displayName} ({old.emailAddress or old.accountId})",
                    f"{new.displayName} ({new.emailAddress or new.accountId})",
                    str(len(filters)),
                )
            console.print(table)
            console.print(f"Total filters matched: {total}")
        else:
            console.print(
                f"Done. Total filters matched: {total}, reassigned: {changed}"
            )

    asyncio.run(main())


@remap.command("issues")
def remap_issues(
    ctx: typer.Context,
    project: Optional[str] = typer.Option(
        None, "--project", help="Optional Jira project key to restrict scope."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Only show counts; no changes."
    ),
):
    """Reassign issues from old -> new users according to the CSV mapping."""
    conf: RemapContext = ctx.obj
    assert isinstance(conf, RemapContext)
    console = conf.console
    progress = conf.progress

    async def main():
        total = 0
        changed = 0
        user_changed = 0
        user_maps = conf.user_maps
        async with get_jira_client(conf) as client:
            with console, progress:

                async def find_issues(field_name: str, user: User) -> list[str]:
                    results = await client.search_issue_keys_for_user_field(
                        field_name, user, project_key=project
                    )
                    progress.update(progress.tasks[-1].id, advance=1)
                    return results

                async def track_task(
                    task_identifier: Task | str,
                    all_tasks: Optional[list[Task]] = None,
                    batch_index: int = 0,
                ) -> Task:
                    nonlocal user_changed
                    if isinstance(task_identifier, str):
                        task = await client.get_task_status(
                            task_id=task_identifier,
                            batch_index=batch_index,
                        )
                    else:
                        task = task_identifier
                        await client.get_task_status(
                            task=task,
                            batch_index=batch_index,
                        )
                    if task.is_finished:
                        user_changed += len(task.processedAccessibleIssues)
                        task.progressPercent = 100
                        if task in pending_tasks:
                            pending_tasks.remove(task)
                    if all_tasks is not None:
                        progress.update(
                            progress.tasks[-1].id,
                            completed=sum(t.progressPercent for t in all_tasks),
                        )
                    else:
                        progress.update(
                            progress.tasks[-1].id, advance=task.progressPercent
                        )
                    return task

                progress.add_task(
                    description="Gathering issues...", total=len(user_maps) * 2
                )
                issues_results = await asyncio.gather(
                    *(
                        find_issues(field, user)
                        for user, _ in user_maps
                        for field in ("assignee", "reporter")
                    )
                )

                issue_maps = [
                    (old, new, assigned_keys, reported_keys)
                    for (old, new), assigned_keys, reported_keys in zip(
                        user_maps,
                        issues_results[0::2],
                        issues_results[1::2],
                    )
                    if assigned_keys or reported_keys
                ]

                progress_user = progress.add_task(
                    description=f"Remapping Users ({len(issue_maps)})...",
                    total=len(issue_maps),
                    visible=not dry_run and len(issue_maps) > 0,
                )

                for old, new, assigned_keys, reported_keys in issue_maps:
                    user_total = len(assigned_keys) + len(reported_keys)
                    total += user_total
                    if dry_run or not user_total:
                        continue

                    progress_issues = progress.add_task(
                        description=f"  {old.emailAddress or old.displayName} -> {new.emailAddress or new.displayName} ({user_total})...",
                        total=None,
                    )
                    bulk_results = await asyncio.gather(
                        client.bulk_update_user_field(
                            issue_keys=assigned_keys,
                            field_name="assignee",
                            new_account_id=new.accountId,
                        ),
                        client.bulk_update_user_field(
                            issue_keys=reported_keys,
                            field_name="reporter",
                            new_account_id=new.accountId,
                        ),
                    )
                    task_ids = list(chain.from_iterable(bulk_results))
                    progress.update(progress_issues, total=len(task_ids) * 100)
                    user_changed = 0
                    pending_tasks = []

                    tasks: list[Task] = await asyncio.gather(
                        *(
                            track_task(tid, batch_index=idx)
                            for idx, tid in enumerate(task_ids)
                        )
                    )

                    pending_tasks = tasks.copy()
                    while pending_tasks:
                        await asyncio.gather(
                            *(
                                track_task(task, tasks, batch_index=idx)
                                for idx, task in enumerate(pending_tasks)
                            )
                        )

                    progress.update(progress_issues, total=100, completed=100)
                    progress.update(progress_user, advance=1)
                    changed += user_changed

        if dry_run:
            table = Table()
            table.add_column("Old User", style="cyan", no_wrap=True)
            table.add_column("Assigned", style="magenta")
            table.add_column("Reported", style="magenta")
            table.add_column("Total", style="green")
            for old, new, assigned, reported in issue_maps:
                table.add_row(
                    f"{old.displayName} ({old.emailAddress or old.accountId})",
                    str(len(assigned)),
                    str(len(reported)),
                    str(len(assigned) + len(reported)),
                )
            console.print(table)
            console.print(f"Total issues matched: {total}")
        else:
            console.print(f"Done. Total issues matched: {total}, reassigned: {changed}")

    asyncio.run(main())


if __name__ == "__main__":
    app()
