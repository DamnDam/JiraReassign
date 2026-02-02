from typing import Optional

from rich.console import Console, RenderableType
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
    TaskProgressColumn,
)


class DualConsole:
    """A Rich console that can log to both stdout and stderr, with error buffering.
    Use as a context manager to buffer error messages until exit on stderr while still printing them to stdout immediately.
    This is useful for ensuring stderr messages do not interfere with Rich progress bars.
    """

    def __init__(self) -> None:
        self._console = Console()
        self._do_buffer = False
        self._err_console = Console(stderr=True)
        self._err_buffer: list[tuple[RenderableType, ...]] = []

    def log_error(
        self, *message: RenderableType, force_flush: Optional[bool] = None
    ) -> None:
        """Log an error message to the standard console and buffer for stderr."""
        if force_flush is None:
            do_flush = not self._do_buffer
        else:
            do_flush = force_flush
        self._err_buffer.append(message)
        if do_flush:
            self.flush_errors()
        else:
            self._console.print(*message)

    def flush_errors(self) -> None:
        """Flush buffered error messages to the error console."""
        for msg in self._err_buffer:
            self._err_console.print(*msg, style="red")
        self._err_buffer.clear()

    @property
    def print(self):
        """Get the standard console print method."""
        return self._console.print

    def __enter__(self):
        self._do_buffer = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._do_buffer = False
        self.flush_errors()

    async def __aenter__(self):
        self._do_buffer = True
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self._do_buffer = False
        self.flush_errors()


def create_progress(console: DualConsole) -> Progress:
    """Create a Rich Progress instance with standard columns."""
    return Progress(
        SpinnerColumn("point"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console._console,
    )
