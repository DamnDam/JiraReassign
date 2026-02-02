# Jira Reassign CLI

A UV-managed Python CLI to bulk replace users in Jira Cloud by reassigning issue assignees.
Fast and concurrent, using async HTTP requests to the Jira REST API.

## Requirements
- [uv](https://github.com/astral-sh/uv) installed
- Jira Cloud site URL and API token

## Configuration
Copy `.env.example` to `.env` and fill in your Jira details.

You can also export the following variables:

```bash
export JIRA_SITE="https://your-domain.atlassian.net"
export JIRA_EMAIL="you@example.com"
export JIRA_API_TOKEN="<your-api-token>"
```

## Install
From the project root, run:

```bash
uv sync
```

## Usage
Run the CLI and see help:

```bash
uv run jtool --help
```

Basic run with a CSV mapping:

```bash
uv run jtool remap mapping.csv issues --project PROJ --concurrency 8
```

Dry run to preview counts:

```bash
uv run jtool remap mapping.csv issues --dry-run
```

CSV format (headers required):

```csv
old,new
old.user@example.com,new.user@example.com
old-account-id,new-account-id
```

## Dev

### Setup dev environment
Sync the environment and install deps with uv:

```bash
uv sync
```

### Setup pre-commit hooks
Pre-commit hooks ensure code quality before each commit. 

Install them with:

```bash
uv run pre-commit install
```

### Run lint, checks and formatter

Optional:
```bash
uv run ruff check
uv run mypy src
```
