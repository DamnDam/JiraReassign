# Jira Reassign CLI

A Python CLI to bulk replace users in Jira Cloud by reassigning issue assignees.
Fast and concurrent, using async HTTP requests to the Jira REST API.

## Requirements

- Python >= 3.11 (or just use [uv](https://github.com/astral-sh/uv)...)
- Jira Cloud site URL and API token

## Install

Just use your favorite python package manager:
```bash
uv tool install git+https://github.com/DamnDam/JiraReassign.git
```

## Configuration

Export the following variables:

```bash
export JTOOL_BASE_URL=https://your-domain.atlassian.net
export JTOOL_EMAIL=you@example.com
export JTOOL_API_TOKEN=your-api-token
```

Or setup a .env file.

## Usage

Run the CLI and see help:
```bash
jtool --help
```

Test the environment parameters:
```bash
jtool --env-file test.env check
```

Basic run with a CSV mapping:
```bash
jtool remap mapping.csv issues --project PROJ
```

Dry run to preview counts:
```bash
jtool remap mapping.csv issues --dry-run
```

CSV format (headers required):
```csv
old,new
old.user@example.com,new.user@example.com
old-account-id,new-account-id
```

## Dev

Use the provided Dev Container configuration for a consistent development environment.

### Setup dev environment

Sync the environment and install deps with uv:
```bash
uv sync
```

Activate the virtual environment:
```bash
source .venv/bin/activate
```

### Run lints, checks and formatters

Optional:
```bash
ruff check
ty src
ruff format
```

### Setup pre-commit hooks

Pre-commit hooks ensure code quality before each push. 

Install them with:

```bash
pre-commit install
```
