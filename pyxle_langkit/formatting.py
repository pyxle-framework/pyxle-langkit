"""Format coordinator for ``.pyx`` files.

Formats Python and JSX sections independently using ``ruff format``
and ``prettier``, then maps edits back to original line positions.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Public types
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextEdit:
    """A text replacement in the original ``.pyx`` file.

    Attributes
    ----------
    start_line:
        1-indexed start line (inclusive).
    end_line:
        1-indexed end line (exclusive).
    new_text:
        Replacement text for the span.
    """

    start_line: int
    end_line: int
    new_text: str


# ------------------------------------------------------------------
# Section parsing (lightweight, no compiler dependency)
# ------------------------------------------------------------------


def _find_sections(
    text: str,
) -> tuple[tuple[str, tuple[int, ...]], tuple[str, tuple[int, ...]]]:
    """Split a .pyx file into Python and JSX sections with line maps.

    Returns ``((python_code, python_line_numbers), (jsx_code, jsx_line_numbers))``.
    Uses the same AST-based boundary detection as the compiler parser.
    """
    import ast as _ast

    lines = text.split("\n")
    n = len(lines)

    python_lines: list[str] = []
    python_line_numbers: list[int] = []
    jsx_lines: list[str] = []
    jsx_line_numbers: list[int] = []

    cursor = 0
    while cursor < n:
        # Try to grow the largest Python prefix.
        end = _find_largest_python_at(lines, cursor, n, _ast)
        if end > cursor:
            for i in range(cursor, end):
                python_lines.append(lines[i])
                python_line_numbers.append(i + 1)  # 1-indexed.
            cursor = end
        else:
            # Grow JSX until Python resumes.
            jsx_end = cursor + 1
            while jsx_end < n:
                probe = _find_largest_python_at(lines, jsx_end, n, _ast)
                if probe > jsx_end:
                    break
                jsx_end += 1

            for i in range(cursor, jsx_end):
                jsx_lines.append(lines[i])
                jsx_line_numbers.append(i + 1)
            cursor = jsx_end

    python_code = "\n".join(python_lines) + ("\n" if python_lines else "")
    jsx_code = "\n".join(jsx_lines) + ("\n" if jsx_lines else "")

    return (
        (python_code, tuple(python_line_numbers)),
        (jsx_code, tuple(jsx_line_numbers)),
    )


def _find_largest_python_at(
    lines: list[str], start: int, n: int, _ast: object,
) -> int:
    """Return the largest k such that lines[start:k] is valid Python."""
    import ast as ast_mod

    if start >= n:
        return start

    rest = "\n".join(lines[start:n])
    if not rest.strip():
        return n

    try:
        ast_mod.parse(rest)
        return n
    except SyntaxError as exc:
        first_failure = (exc.lineno or 1) - 1

    upper = min(first_failure + 1, n - start)
    while upper > 0:
        prefix = "\n".join(lines[start : start + upper])
        if not prefix.strip():
            return start
        try:
            ast_mod.parse(prefix)
            return start + upper
        except SyntaxError:
            upper -= 1

    return start


# ------------------------------------------------------------------
# Subprocess formatting
# ------------------------------------------------------------------


async def _run_formatter(
    cmd: Sequence[str],
    input_text: str,
    *,
    timeout: float = 30.0,
) -> str | None:
    """Run a formatter subprocess and return its stdout.

    Returns ``None`` on any failure (missing binary, non-zero exit,
    timeout). Logs the failure at INFO level.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=input_text.encode()),
            timeout=timeout,
        )
    except FileNotFoundError:
        logger.info("Formatter not found: %s", cmd[0])
        return None
    except asyncio.TimeoutError:
        logger.info("Formatter timed out: %s", cmd[0])
        proc.kill()  # type: ignore[union-attr]
        return None
    except OSError as exc:
        logger.info("Formatter failed to start: %s — %s", cmd[0], exc)
        return None

    if proc.returncode != 0:
        logger.info(
            "Formatter %s exited with code %d: %s",
            cmd[0],
            proc.returncode,
            stderr.decode(errors="replace").strip(),
        )
        return None

    return stdout.decode()


async def _format_python(
    code: str,
    *,
    path: Path | None = None,
    formatter: str = "ruff",
) -> str | None:
    """Format Python code via ruff or black."""
    if not code.strip():
        return None

    if formatter == "ruff":
        binary = shutil.which("ruff")
        if binary is None:
            logger.info("ruff not found in PATH; skipping Python formatting")
            return None
        cmd = [binary, "format", "--stdin-filename", str(path or "stdin.py"), "-"]
    elif formatter == "black":
        binary = shutil.which("black")
        if binary is None:
            logger.info("black not found in PATH; skipping Python formatting")
            return None
        cmd = [binary, "--stdin-filename", str(path or "stdin.py"), "-"]
    else:
        logger.info("Unknown Python formatter: %s", formatter)
        return None

    return await _run_formatter(cmd, code)


async def _format_jsx(
    code: str,
    *,
    formatter: str = "prettier",
) -> str | None:
    """Format JSX code via prettier."""
    if not code.strip():
        return None

    if formatter == "prettier":
        binary = shutil.which("prettier")
        if binary is None:
            logger.info("prettier not found in PATH; skipping JSX formatting")
            return None
        cmd = [binary, "--parser=babel", "--stdin-filepath", "stdin.jsx"]
    else:
        logger.info("Unknown JSX formatter: %s", formatter)
        return None

    return await _run_formatter(cmd, code)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


async def format_document(
    text: str,
    *,
    path: Path | None = None,
    python_formatter: str = "ruff",
    jsx_formatter: str = "prettier",
) -> tuple[TextEdit, ...]:
    """Format a ``.pyx`` file by formatting each section independently.

    Returns a tuple of ``TextEdit`` objects that, when applied in order,
    transform the original text into the formatted version.

    If a formatter is not installed, that section is silently skipped.
    """
    (python_code, python_line_numbers), (jsx_code, jsx_line_numbers) = _find_sections(text)

    # Run both formatters concurrently.
    python_result, jsx_result = await asyncio.gather(
        _format_python(python_code, path=path, formatter=python_formatter),
        _format_jsx(jsx_code, formatter=jsx_formatter),
    )

    edits: list[TextEdit] = []

    if python_result is not None and python_result != python_code:
        edit = _build_section_edit(python_result, python_line_numbers)
        if edit is not None:
            edits.append(edit)

    if jsx_result is not None and jsx_result != jsx_code:
        edit = _build_section_edit(jsx_result, jsx_line_numbers)
        if edit is not None:
            edits.append(edit)

    # Sort edits by start line (descending) so applying them
    # bottom-up avoids line number shifts.
    edits.sort(key=lambda e: e.start_line, reverse=True)

    return tuple(edits)


def _build_section_edit(
    formatted_code: str,
    line_numbers: tuple[int, ...],
) -> TextEdit | None:
    """Build a TextEdit replacing a contiguous section span."""
    if not line_numbers:
        return None

    start_line = line_numbers[0]
    end_line = line_numbers[-1] + 1  # Exclusive.

    return TextEdit(
        start_line=start_line,
        end_line=end_line,
        new_text=formatted_code,
    )
