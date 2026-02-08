# JiraReassign CLI - AI Agent Instructions

## Project Overview
This is a Python CLI tool (`jtool`) that bulk-reassigns Jira Cloud users using async HTTP requests. It processes CSV mappings to replace old user assignments with new ones across Jira issues, filters, and Confluence spaces.

## Architecture & Key Components

**CLI Structure** (Typer-based, hierarchical commands)
- `src/jtool/cli/main.py`: Root commands (`check`, `find`) and app initialization
- `src/jtool/cli/remap.py`: Core remapping functionality (`remap issues`, `remap filters`, `remap spaces`)
- `src/jtool/cli/base.py`: Shared CLI context pattern using `CLIContext` dataclass

**API Client Architecture** (async, rate-limited)
- `src/jtool/client/base.py`: `BaseClient` with semaphore-based concurrency control
- `src/jtool/client/jira.py`: Jira-specific API operations and models
- `src/jtool/client/confluence.py`: Confluence-specific operations
- All clients use `@handle_api_errors` decorator for consistent error handling

**Configuration & Environment**
- `src/jtool/config.py`: Pydantic Settings with `JTOOL_*` env vars (base_url, email, api_token, concurrency)
- Factory method: `settings.get_client(JiraClient)` instantiates configured clients

## Critical Patterns

**Async Concurrency Control**
```python
async with self._rate_limit(stagger_order=i):
    # API call here
```
Always use the rate-limiting context manager to prevent API throttling.

**CSV Processing Pattern**
- Headers must be `old,new` (validated in `remap_callback`)
- User resolution happens concurrently before any operations
- Failed resolutions are logged but don't stop processing

**CLI Context Propagation**
```python
@dataclass
class CLIContext:
    console: Console
    settings: Settings

# Access in commands:
conf = cast(CLIContext, ctx.obj)
```

**Error Handling**
- API errors are wrapped in `APIError`/`APIHTTPError` with request/response details
- Jira-specific errors extract messages from `errorMessages` and `errors` fields
- Use `@handle_jira_errors` decorator on all Jira client methods

**Rich Console Integration**
- Custom `Console` class wraps Rich console + progress + logging
- Use `console.progress` for long operations, not raw Rich Progress
- Buffered logging handler for clean progress display

## Entry Points & Commands
- `jtool check`: Test API connectivity
- `jtool find <identifiers>`: Resolve user identifiers to account details
- `jtool remap <mapping.csv> issues --project PROJ [--dry-run]`: Bulk reassign issues
- `jtool remap <mapping.csv> filters [--dry-run]`: Transfer filter ownership
- `jtool remap <mapping.csv> spaces [--dry-run]`: Update Confluence permissions

## Development Workflow
- All commands run via `uv run` (not direct python)
- Pre-commit hooks run on pre-push: `uv run pre-commit run --all-files --hook-stage pre-push`
- Linting: `uv run ruff check`, Formatting: `uv run ruff format`, Types: `uv run ty check`

## Key Files
- `pyproject.toml`: Defines `jtool` as console script entry point
- `examples/mapping.csv`: CSV format reference
- `src/jtool/term.py`: Custom Rich console with buffered logging
- `ruff.toml`: 100-char line limit, `src/` focus
- `ty.toml`: Type checking with `src/` root
