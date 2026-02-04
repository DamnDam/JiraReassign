from typing import Iterable, Tuple
import logging

from rich.logging import RichHandler
from rich.console import Console as RichConsole, RenderableType
from rich.table import Table
from rich.style import Style
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
    TaskProgressColumn,
)


class BufferedHandler(RichHandler):
    """A logging handler that buffers log records until flushed."""

    _buffer: list[logging.LogRecord]
    auto_flush: bool = True

    def _get_buffer(self) -> list[logging.LogRecord]:
        if not hasattr(self, "_buffer"):
            self._buffer = []
        return self._buffer

    def emit(self, record: logging.LogRecord) -> None:
        if self.auto_flush:
            super().emit(record)
        else:
            self._get_buffer().append(record)

    def flush(self) -> None:
        for record in self._get_buffer():
            super().emit(record)
        self._get_buffer().clear()


class Console:
    """A Rich console wrapper for logging and table rendering."""

    _console: RichConsole
    _progress: Progress
    _handler: BufferedHandler

    def __init__(self) -> None:
        self._console = RichConsole()
        self._handler = BufferedHandler(
            console=self._console,
            show_time=False,
            show_path=False,
        )

    def add_logger(self, logger: logging.Logger) -> None:
        """Add a logger to the internal BufferedHandler."""
        logger.addHandler(self._handler)

    @property
    def print(self):
        """Get the standard console print method."""
        return self._console.print

    def render_table(
        self,
        headers: Iterable[Tuple[RenderableType, str | Style | None]],
        iter: Iterable[Tuple[RenderableType, ...]],
    ) -> None:
        """Render a table to the standard console."""
        table = Table(show_header=True, header_style="bold")
        for header in headers:
            table.add_column(header[0], style=header[1])
        for row in iter:
            table.add_row(*[str(cell) for cell in row])
        self._console.print(table)

    @property
    def progress(self) -> Progress:
        """Create a Rich Progress instance with standard columns."""
        if not hasattr(self, "_progress"):
            self._progress = Progress(
                SpinnerColumn("point"),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=self._console,
            )
        return self._progress

    def __enter__(self):
        self._handler.auto_flush = False
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._handler.flush()
        self._handler.auto_flush = True

    async def __aenter__(self):
        self._handler.auto_flush = False
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self._handler.flush()
        self._handler.auto_flush = True
