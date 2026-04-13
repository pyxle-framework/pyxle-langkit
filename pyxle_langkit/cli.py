"""Command-line interface for Pyxle language tools.

Provides ``parse``, ``lint``, ``outline``, and ``format`` subcommands
for use outside of an editor.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from .linter import PyxLinter
from .parser_adapter import TolerantParser
from .symbols import extract_document_symbols

app = typer.Typer(
    name="pyxle-langkit",
    help="Language tools for Pyxle .pyxl files.",
    no_args_is_help=True,
)


# ------------------------------------------------------------------
# parse
# ------------------------------------------------------------------


@app.command()
def parse(
    file: Path = typer.Argument(..., exists=True, help="Path to a .pyxl file."),
) -> None:
    """Parse a .pyxl file and output its structure as JSON."""
    parser = TolerantParser()
    document = parser.parse(file)

    output = {
        "path": str(document.path),
        "has_python": document.has_python,
        "has_jsx": document.has_jsx,
        "python_lines": len(document.python_code.splitlines()),
        "jsx_lines": len(document.jsx_code.splitlines()),
        "loader": (
            {
                "name": document.loader.name,
                "line": document.loader.line_number,
                "is_async": document.loader.is_async,
                "parameters": list(document.loader.parameters),
            }
            if document.loader
            else None
        ),
        "actions": [
            {
                "name": a.name,
                "line": a.line_number,
                "is_async": a.is_async,
                "parameters": list(a.parameters),
            }
            for a in document.actions
        ],
        "head_elements": list(document.head_elements),
        "head_is_dynamic": document.head_is_dynamic,
        "diagnostics": [
            {
                "section": d.section,
                "severity": d.severity,
                "message": d.message,
                "line": d.line,
            }
            for d in document.diagnostics
        ],
    }

    typer.echo(json.dumps(output, indent=2))


# ------------------------------------------------------------------
# lint
# ------------------------------------------------------------------


@app.command()
def lint(
    file: Path = typer.Argument(..., exists=True, help="Path to a .pyxl file."),
) -> None:
    """Lint a .pyxl file and print diagnostics."""
    parser = TolerantParser()
    document = parser.parse(file)
    linter = PyxLinter()
    issues = linter.lint(document)

    # Also include parser diagnostics.
    has_errors = False

    for diag in document.diagnostics:
        severity = diag.severity.upper()
        line = diag.line or "?"
        color = typer.colors.RED if diag.severity == "error" else typer.colors.YELLOW
        typer.secho(
            f"  {line:>5}  {severity:<8}  [{diag.section}] {diag.message}",
            fg=color,
        )
        if diag.severity == "error":
            has_errors = True

    for issue in issues:
        severity = issue.severity.upper()
        color = (
            typer.colors.RED
            if issue.severity == "error"
            else typer.colors.YELLOW
            if issue.severity == "warning"
            else typer.colors.CYAN
        )
        typer.secho(
            f"  {issue.line:>5}  {severity:<8}  [{issue.rule}] {issue.message}",
            fg=color,
        )
        if issue.severity == "error":
            has_errors = True

    total = len(issues) + len(document.diagnostics)
    if total == 0:
        typer.secho("  No issues found.", fg=typer.colors.GREEN)
    else:
        typer.echo(f"\n  {total} issue(s) found.")

    if has_errors:
        raise typer.Exit(code=1)


# ------------------------------------------------------------------
# outline
# ------------------------------------------------------------------


@app.command()
def outline(
    file: Path = typer.Argument(..., exists=True, help="Path to a .pyxl file."),
) -> None:
    """Show the symbol outline of a .pyxl file."""
    parser = TolerantParser()
    document = parser.parse(file)
    symbols = extract_document_symbols(document)

    if not symbols:
        typer.echo("  (no symbols)")
        return

    for sym in symbols:
        detail = f"  ({sym.detail})" if sym.detail else ""
        typer.echo(f"  {sym.line:>5}  {sym.kind:<16}  {sym.name}{detail}")


# ------------------------------------------------------------------
# format
# ------------------------------------------------------------------


@app.command(name="format")
def format_cmd(
    file: Path = typer.Argument(..., exists=True, help="Path to a .pyxl file."),
    python_formatter: str = typer.Option("ruff", help="Python formatter: ruff, black, none."),
    jsx_formatter: str = typer.Option("prettier", help="JSX formatter: prettier, none."),
    check: bool = typer.Option(False, "--check", help="Check if file would be changed."),
) -> None:
    """Format a .pyxl file."""
    from .formatting import format_document

    text = file.read_text(encoding="utf-8")
    edits = asyncio.run(
        format_document(
            text,
            path=file,
            python_formatter=python_formatter,
            jsx_formatter=jsx_formatter,
        )
    )

    if not edits:
        typer.echo(f"  {file}: already formatted")
        return

    if check:
        typer.secho(f"  {file}: would be reformatted", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    # Apply edits to the original text.
    lines = text.splitlines(keepends=True)
    # Apply edits in reverse order so line numbers stay valid.
    for edit in sorted(edits, key=lambda e: e.start_line, reverse=True):
        start_idx = edit.start_line - 1
        end_idx = edit.end_line - 1
        new_lines = (edit.new_text + "\n").splitlines(keepends=True)
        lines[start_idx:end_idx] = new_lines

    formatted = "".join(lines)
    file.write_text(formatted, encoding="utf-8")
    typer.secho(f"  {file}: reformatted", fg=typer.colors.GREEN)


if __name__ == "__main__":  # pragma: no cover
    app()
