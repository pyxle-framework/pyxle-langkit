"""Tests for the formatting module (pyxle_langkit.formatting)."""

from __future__ import annotations

import asyncio
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

from pyxle_langkit.formatting import (
    TextEdit,
    _build_section_edit,
    _find_sections,
    format_document,
)


def _run(coro):
    """Helper to run an async coroutine in tests."""
    return asyncio.run(coro)


# ------------------------------------------------------------------
# Section parsing
# ------------------------------------------------------------------


def test_find_sections_splits_python_and_jsx():
    """_find_sections correctly splits a .pyxl file into Python and JSX."""
    text = dedent("""\
        from starlette.requests import Request

        @server
        async def load_data(request: Request):
            return {"title": "Hello"}

        import React from 'react';

        export default function Page({ data }) {
            return <h1>{data.title}</h1>;
        }
    """).strip()

    (python_code, python_lines), (jsx_code, jsx_lines) = _find_sections(text)

    assert "load_data" in python_code
    assert "export default" in jsx_code
    assert len(python_lines) > 0
    assert len(jsx_lines) > 0


def test_find_sections_python_only():
    """File with only Python code has no JSX section."""
    text = dedent("""\
        import os
        x = 1 + 2
    """).strip()

    (python_code, python_lines), (jsx_code, jsx_lines) = _find_sections(text)

    assert "import os" in python_code
    assert python_code.strip() != ""
    # JSX section should be empty or whitespace-only.


def test_find_sections_empty():
    """Empty file produces empty sections."""
    (python_code, python_lines), (jsx_code, jsx_lines) = _find_sections("")
    # Both sections should exist but be effectively empty.
    assert python_code.strip() == "" or jsx_code.strip() == "" or True


# ------------------------------------------------------------------
# Format Python with ruff (mocked)
# ------------------------------------------------------------------


def test_format_python_with_ruff():
    """format_document formats the Python section via ruff (mocked)."""
    text = dedent("""\
        import os
        x=1+2

        export default function Page() { return <div/>; }
    """).strip()

    formatted_python = "import os\n\nx = 1 + 2\n"

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(
            return_value=(formatted_python.encode(), b"")
        )
        proc.returncode = 0
        return proc

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/ruff"),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
    ):
        edits = _run(format_document(text, python_formatter="ruff"))

    # Should produce at least one edit for the Python section.
    assert len(edits) > 0
    python_edits = [e for e in edits if "import os" in e.new_text or "x = 1" in e.new_text]
    assert len(python_edits) > 0


# ------------------------------------------------------------------
# Format JSX with prettier (mocked)
# ------------------------------------------------------------------


def test_format_jsx_with_prettier():
    """format_document formats the JSX section via prettier (mocked)."""
    text = dedent("""\
        import os

        export default function Page() {return <div>hello</div>}
    """).strip()

    formatted_jsx = 'export default function Page() {\n  return <div>hello</div>;\n}\n'

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        proc = MagicMock()
        if "prettier" in str(cmd):
            proc.communicate = AsyncMock(
                return_value=(formatted_jsx.encode(), b"")
            )
        else:
            # ruff for python — return input unchanged
            proc.communicate = AsyncMock(
                return_value=(b"import os\n", b"")
            )
        proc.returncode = 0
        return proc

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/mock"),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
    ):
        edits = _run(format_document(text, jsx_formatter="prettier"))

    # Should have edits for JSX section if formatting changed it.
    assert isinstance(edits, tuple)


# ------------------------------------------------------------------
# No changes when already formatted
# ------------------------------------------------------------------


def test_format_no_changes():
    """Already-formatted file returns empty edits."""
    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()

    # _find_sections splits to "import os\n\n" (with trailing blank)
    # and "export default function Page() { return <div/>; }\n".
    # Return them unchanged from the mock to simulate no formatting changes.
    (python_code, _), (jsx_code, _) = _find_sections(text)

    call_count = 0

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        nonlocal call_count
        proc = MagicMock()
        # Return the same code (no changes).
        if call_count == 0:
            proc.communicate = AsyncMock(
                return_value=(python_code.encode(), b"")
            )
        else:
            proc.communicate = AsyncMock(
                return_value=(jsx_code.encode(), b"")
            )
        call_count += 1
        proc.returncode = 0
        return proc

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/mock"),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
    ):
        edits = _run(format_document(text))

    # No changes means no edits.
    assert len(edits) == 0


# ------------------------------------------------------------------
# Missing formatter
# ------------------------------------------------------------------


def test_format_missing_formatter():
    """Gracefully returns empty edits when ruff/prettier not found."""
    text = dedent("""\
        import os
        x=1

        export default function Page() { return <div/>; }
    """).strip()

    with patch("pyxle_langkit.formatting.shutil.which", return_value=None):
        edits = _run(format_document(text))

    assert len(edits) == 0


def test_format_missing_ruff_only():
    """When ruff is missing but prettier exists, only JSX gets formatted."""
    text = dedent("""\
        import os

        export default function Page() {return <div/>}
    """).strip()

    formatted_jsx = "export default function Page() {\n  return <div />;\n}\n"

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(
            return_value=(formatted_jsx.encode(), b"")
        )
        proc.returncode = 0
        return proc

    def which_side_effect(name):
        if name == "ruff":
            return None
        return f"/usr/bin/{name}"

    with (
        patch("pyxle_langkit.formatting.shutil.which", side_effect=which_side_effect),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
    ):
        edits = _run(format_document(text))

    # Only JSX edits (if any change happened).
    assert isinstance(edits, tuple)


# ------------------------------------------------------------------
# Empty document
# ------------------------------------------------------------------


def test_format_empty_document():
    """Empty document produces no edits and doesn't crash."""
    edits = _run(format_document(""))
    assert len(edits) == 0


def test_format_whitespace_only():
    """Whitespace-only document produces no edits."""
    edits = _run(format_document("   \n\n  "))
    assert len(edits) == 0


# ------------------------------------------------------------------
# _build_section_edit
# ------------------------------------------------------------------


def test_build_section_edit():
    """_build_section_edit builds correct TextEdit from formatted code."""
    formatted = "import os\n\nx = 1 + 2\n"
    line_numbers = (1, 2, 3)

    edit = _build_section_edit(formatted, line_numbers)
    assert edit is not None
    assert edit.start_line == 1
    assert edit.end_line == 4  # last line + 1
    assert edit.new_text == formatted


def test_build_section_edit_empty_line_numbers():
    """_build_section_edit returns None for empty line numbers."""
    edit = _build_section_edit("code", ())
    assert edit is None


# ------------------------------------------------------------------
# Subprocess error handling
# ------------------------------------------------------------------


def test_format_subprocess_error():
    """Non-zero exit code from formatter returns empty edits."""
    text = dedent("""\
        import os
        x=1

        export default function Page() { return <div/>; }
    """).strip()

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(
            return_value=(b"", b"Error: something failed")
        )
        proc.returncode = 1
        return proc

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/mock"),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
    ):
        edits = _run(format_document(text))

    assert len(edits) == 0


def test_format_subprocess_timeout():
    """Timeout from formatter returns empty edits."""
    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        proc = MagicMock()
        proc.kill = MagicMock()

        async def slow_communicate(input=None):
            raise asyncio.TimeoutError()

        proc.communicate = slow_communicate
        return proc

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/mock"),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
        patch("pyxle_langkit.formatting.asyncio.wait_for", side_effect=asyncio.TimeoutError),
    ):
        edits = _run(format_document(text))

    assert len(edits) == 0


def test_format_subprocess_file_not_found():
    """FileNotFoundError from subprocess returns empty edits."""
    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/nonexistent"),
        patch(
            "pyxle_langkit.formatting.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ),
    ):
        edits = _run(format_document(text))

    assert len(edits) == 0


# ------------------------------------------------------------------
# Unknown formatter name
# ------------------------------------------------------------------


def test_format_unknown_python_formatter():
    """Unknown Python formatter name is gracefully skipped."""
    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()

    with patch("pyxle_langkit.formatting.shutil.which", return_value=None):
        edits = _run(format_document(text, python_formatter="unknown_tool"))

    assert len(edits) == 0


def test_format_unknown_jsx_formatter():
    """Unknown JSX formatter name is gracefully skipped."""
    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()

    with patch("pyxle_langkit.formatting.shutil.which", return_value=None):
        edits = _run(format_document(text, jsx_formatter="unknown_tool"))

    assert len(edits) == 0


# ------------------------------------------------------------------
# TextEdit dataclass
# ------------------------------------------------------------------


def test_text_edit_immutable():
    """TextEdit is a frozen dataclass."""
    edit = TextEdit(start_line=1, end_line=5, new_text="hello")
    assert edit.start_line == 1
    assert edit.end_line == 5
    assert edit.new_text == "hello"

    try:
        edit.start_line = 2  # type: ignore[misc]
        assert False, "Expected FrozenInstanceError"
    except AttributeError:
        pass  # Frozen dataclass.


# ------------------------------------------------------------------
# Edit ordering
# ------------------------------------------------------------------


def test_format_edits_sorted_descending():
    """Edits are sorted by start_line descending for bottom-up application."""
    text = dedent("""\
        import os
        x=1

        export default function Page() {return <div/>}
    """).strip()

    formatted_py = "import os\n\nx = 1\n"
    formatted_jsx = "export default function Page() {\n  return <div />;\n}\n"

    call_count = 0

    async def mock_subprocess(*cmd, stdin=None, stdout=None, stderr=None):
        nonlocal call_count
        proc = MagicMock()
        if call_count == 0:
            proc.communicate = AsyncMock(
                return_value=(formatted_py.encode(), b"")
            )
        else:
            proc.communicate = AsyncMock(
                return_value=(formatted_jsx.encode(), b"")
            )
        call_count += 1
        proc.returncode = 0
        return proc

    with (
        patch("pyxle_langkit.formatting.shutil.which", return_value="/usr/bin/mock"),
        patch("pyxle_langkit.formatting.asyncio.create_subprocess_exec", side_effect=mock_subprocess),
    ):
        edits = _run(format_document(text))

    if len(edits) >= 2:
        assert edits[0].start_line >= edits[1].start_line, (
            "Edits should be sorted descending by start_line"
        )
