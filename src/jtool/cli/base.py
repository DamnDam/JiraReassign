import logging
from dataclasses import dataclass


from jtool.config import Settings
from jtool.term import Console

logger = logging.getLogger("jtool.cli")


@dataclass
class CLIContext:
    """Context for CLI commands."""

    console: Console
    settings: Settings
