"""Diagnostic aggregation and mapping.

Converts compiler diagnostics, lint issues, and compilation errors into
LSP ``Diagnostic`` objects for IDE consumption.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Sequence

from lsprotocol.types import (
    Diagnostic,
    DiagnosticSeverity,
    Position,
    Range,
)

from pyxle.compiler.exceptions import CompilationError
from pyxle.compiler.parser import PyxDiagnostic

if TYPE_CHECKING:
    from pyxle_langkit.linter import LintIssue

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Severity mapping
# ------------------------------------------------------------------

_SEVERITY_MAP: dict[str, DiagnosticSeverity] = {
    "error": DiagnosticSeverity.Error,
    "warning": DiagnosticSeverity.Warning,
    "info": DiagnosticSeverity.Information,
}


def _map_severity(severity: str) -> DiagnosticSeverity:
    """Map a string severity to an LSP ``DiagnosticSeverity``.

    Falls back to ``Warning`` for unrecognised values.
    """
    return _SEVERITY_MAP.get(severity, DiagnosticSeverity.Warning)


def _safe_line(line: int | None) -> int:
    """Convert a possibly-None 1-indexed line to a 0-indexed line.

    LSP positions are 0-indexed. Source diagnostics use 1-indexed lines.
    Returns 0 when the line is unknown.
    """
    if line is None or line < 1:
        return 0
    return line - 1


def _safe_column(column: int | None) -> int:
    """Convert a possibly-None 1-indexed column to a 0-indexed column.

    Returns 0 when the column is unknown.
    """
    if column is None or column < 1:
        return 0
    return column - 1


# ------------------------------------------------------------------
# Public conversion functions
# ------------------------------------------------------------------


def lint_issues_to_lsp_diagnostics(
    issues: Sequence[LintIssue],
) -> list[Diagnostic]:
    """Convert ``LintIssue`` instances to LSP ``Diagnostic`` objects.

    Each issue is tagged with source ``"pyxle-linter"`` unless it
    originated from an external tool (pyflakes, babel), in which case
    the original source name is preserved.
    """
    diagnostics: list[Diagnostic] = []
    for issue in issues:
        line = _safe_line(issue.line)
        col = _safe_column(getattr(issue, "column", None))
        end_col = col + getattr(issue, "length", 1)
        source = getattr(issue, "source", None) or "pyxle-linter"

        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=line, character=col),
                    end=Position(line=line, character=end_col),
                ),
                message=issue.message,
                severity=_map_severity(issue.severity),
                source=source,
                code=getattr(issue, "code", None),
            )
        )
    return diagnostics


def compilation_error_to_lsp_diagnostic(exc: CompilationError) -> Diagnostic:
    """Convert a ``CompilationError`` to an LSP ``Diagnostic``."""
    line = _safe_line(exc.line_number)
    return Diagnostic(
        range=Range(
            start=Position(line=line, character=0),
            end=Position(line=line, character=0),
        ),
        message=exc.message,
        severity=DiagnosticSeverity.Error,
        source="pyxle-parser",
    )


def parser_diagnostics_to_lsp(
    diagnostics: Sequence[PyxDiagnostic],
) -> list[Diagnostic]:
    """Convert ``PyxDiagnostic`` entries to LSP ``Diagnostic`` objects.

    Source is tagged based on the diagnostic section: ``"pyxle-parser"``
    for Python section errors, ``"babel"`` for JSX section errors.
    """
    result: list[Diagnostic] = []
    for diag in diagnostics:
        line = _safe_line(diag.line)
        col = _safe_column(diag.column)
        source = "babel" if diag.section == "jsx" else "pyxle-parser"

        result.append(
            Diagnostic(
                range=Range(
                    start=Position(line=line, character=col),
                    end=Position(line=line, character=col),
                ),
                message=diag.message,
                severity=_map_severity(diag.severity),
                source=source,
            )
        )
    return result
