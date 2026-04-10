"""Tolerant parser wrapper for IDE use.

Wraps ``pyxle.compiler.parser.PyxParser`` to ensure it **never** raises
``CompilationError``. All exceptions are caught and converted to
``PyxDiagnostic`` entries on the returned ``PyxDocument``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pyxle.compiler.exceptions import CompilationError
from pyxle.compiler.parser import PyxDiagnostic, PyxParser

from .document import PyxDocument

logger = logging.getLogger(__name__)


class TolerantParser:
    """Tolerant wrapper around :class:`PyxParser`.

    Always parses with ``tolerant=True`` so syntax errors are collected
    as diagnostics rather than raised. Additionally catches any
    unexpected exception from the parser and converts it to a
    diagnostic, guaranteeing that a ``PyxDocument`` is always returned.
    """

    def __init__(self) -> None:
        self._parser = PyxParser()

    def parse(self, path: Path) -> PyxDocument:
        """Parse a ``.pyx`` file from disk.

        Reads the file and delegates to :meth:`parse_text`. If the file
        cannot be read, returns an empty document with a diagnostic.
        """
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.debug("Failed to read %s: %s", path, exc)
            diagnostic = PyxDiagnostic(
                section="python",
                severity="error",
                message=f"Cannot read file: {exc}",
                line=None,
            )
            return _empty_document(path=path, diagnostics=(diagnostic,))

        return self.parse_text(text, path=path)

    def parse_text(self, text: str, path: Path | None = None) -> PyxDocument:
        """Parse a ``.pyx`` source string.

        Never raises. Any exception from the underlying parser is caught
        and surfaced as a diagnostic on the returned document.
        """
        try:
            result = self._parser.parse_text(text, tolerant=True)
        except CompilationError as exc:
            # Should not happen with tolerant=True, but defend against it.
            logger.debug("CompilationError despite tolerant=True: %s", exc)
            diagnostic = PyxDiagnostic(
                section="python",
                severity="error",
                message=exc.message,
                line=exc.line_number,
            )
            return _empty_document(path=path, source=text, diagnostics=(diagnostic,))
        except Exception as exc:
            # Catch-all for truly unexpected failures (e.g. bugs in the
            # parser, missing Node.js for Babel, etc.).
            logger.debug("Unexpected parser error on %s: %s", path, exc)
            diagnostic = PyxDiagnostic(
                section="python",
                severity="error",
                message=f"Internal parser error: {type(exc).__name__}: {exc}",
                line=None,
            )
            return _empty_document(path=path, source=text, diagnostics=(diagnostic,))

        return PyxDocument(
            path=path,
            source=text,
            python_code=result.python_code,
            jsx_code=result.jsx_code,
            python_line_numbers=tuple(result.python_line_numbers),
            jsx_line_numbers=tuple(result.jsx_line_numbers),
            loader=result.loader,
            actions=result.actions,
            head_elements=result.head_elements,
            head_is_dynamic=result.head_is_dynamic,
            diagnostics=result.diagnostics,
            script_declarations=result.script_declarations,
            image_declarations=result.image_declarations,
            head_jsx_blocks=result.head_jsx_blocks,
        )


def _empty_document(
    *,
    path: Path | None = None,
    source: str = "",
    diagnostics: tuple[PyxDiagnostic, ...] = (),
) -> PyxDocument:
    """Create an empty ``PyxDocument`` with the given diagnostics."""
    return PyxDocument(
        path=path,
        source=source,
        python_code="",
        jsx_code="",
        python_line_numbers=(),
        jsx_line_numbers=(),
        loader=None,
        actions=(),
        head_elements=(),
        head_is_dynamic=False,
        diagnostics=diagnostics,
        script_declarations=(),
        image_declarations=(),
        head_jsx_blocks=(),
    )
