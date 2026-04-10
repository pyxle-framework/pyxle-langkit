"""Tests for the completion provider (pyxle_langkit.completions)."""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import MagicMock, patch

from lsprotocol.types import CompletionItemKind, InsertTextFormat

from pyxle_langkit.completions import CompletionProvider
from pyxle_langkit.parser_adapter import TolerantParser

SAMPLE = dedent("""\
    from starlette.requests import Request

    @server
    async def load_data(request: Request):
        return {"title": "Hello", "count": 42}

    import React from 'react';

    export default function Page({ data }) {
        return <h1>{data.title}</h1>;
    }
""").strip()

_parser = TolerantParser()


def _parse(text: str = SAMPLE):
    return _parser.parse_text(text)


# ------------------------------------------------------------------
# Python completions via Jedi
# ------------------------------------------------------------------


def test_python_completions_via_jedi():
    """Cursor in Python section returns Jedi completions (mocked)."""
    provider = CompletionProvider()

    simple_py = dedent("""\
        import os
        result = os.path

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(simple_py)

    # Mock jedi.Script so we control the completion results.
    mock_completion = MagicMock()
    mock_completion.name = "join"
    mock_completion.type = "function"
    mock_completion.description = "Join path components"

    mock_script = MagicMock()
    mock_script.complete.return_value = [mock_completion]

    with patch("pyxle_langkit.completions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        items = provider.complete(doc, line=2, column=13)

    labels = [item.label for item in items]
    assert "join" in labels, f"Expected 'join' in completions, got: {labels}"
    assert items[0].kind == CompletionItemKind.Function


# ------------------------------------------------------------------
# JSX completions for Pyxle components
# ------------------------------------------------------------------


def test_jsx_completions_pyxle_components():
    """Cursor after '<' in JSX returns Pyxle component completions."""
    doc = _parse()
    provider = CompletionProvider()

    # In the JSX section, simulate typing "<" at the start of a new line.
    # The JSX section starts at line 7 in the original .pyx source.
    # We need a line in the JSX section that has "<" at the cursor.
    jsx_with_tag = dedent("""\
        from starlette.requests import Request

        @server
        async def load_data(request: Request):
            return {"title": "Hello"}

        import React from 'react';

        export default function Page({ data }) {
            return <
        }
    """).strip()
    doc = _parse(jsx_with_tag)

    # Line 10 in original source: "    return <"
    # The "<" is at col 11, cursor after it at col 12.
    items = provider.complete(doc, line=10, column=12)
    labels = [item.label for item in items]
    expected_components = {"Link", "Script", "Image", "Head", "Slot", "ClientOnly", "Form"}
    assert expected_components.issubset(set(labels)), (
        f"Expected Pyxle components in completions, got: {labels}"
    )

    # Verify snippets: container components use closing tags.
    head_item = next(i for i in items if i.label == "Head")
    assert head_item.insert_text_format == InsertTextFormat.Snippet
    assert "</Head>" in head_item.insert_text

    link_item = next(i for i in items if i.label == "Link")
    assert link_item.insert_text_format == InsertTextFormat.Snippet
    assert "/>" in link_item.insert_text


# ------------------------------------------------------------------
# data. completions
# ------------------------------------------------------------------


def test_data_dot_completions():
    """'data.' in JSX returns keys from the loader return dict."""
    doc = _parse()
    provider = CompletionProvider()

    # Line 11 in SAMPLE: "    return <h1>{data.title}</h1>;"
    # Find the JSX line with "data."
    jsx_line_with_data = dedent("""\
        from starlette.requests import Request

        @server
        async def load_data(request: Request):
            return {"title": "Hello", "count": 42}

        import React from 'react';

        export default function Page({ data }) {
            return <h1>{data.}</h1>;
        }
    """).strip()
    doc = _parse(jsx_line_with_data)

    # "data." is on line 10 ("    return <h1>{data.}</h1>;")
    # Find position of "data." — column after the dot.
    # "    return <h1>{data." -> col 24 roughly.
    line_text = "    return <h1>{data.}</h1>;"
    col = line_text.index("data.") + len("data.")
    items = provider.complete(doc, line=10, column=col)
    labels = [item.label for item in items]
    assert "title" in labels, f"Expected 'title' in data completions, got: {labels}"
    assert "count" in labels, f"Expected 'count' in data completions, got: {labels}"

    for item in items:
        assert item.kind == CompletionItemKind.Property


# ------------------------------------------------------------------
# Unknown section returns empty
# ------------------------------------------------------------------


def test_unknown_section_returns_empty():
    """Cursor between sections returns empty completions."""
    # Create a document with a blank line between python and jsx.
    # Use line 99 which is far past the end of the document — guaranteed
    # to be "unknown" section.
    doc = _parse()
    provider = CompletionProvider()

    items = provider.complete(doc, line=99, column=0)
    assert len(items) == 0, f"Expected empty completions for out-of-range line, got: {len(items)}"


# ------------------------------------------------------------------
# Pyxle snippets in Python section
# ------------------------------------------------------------------


def test_pyxle_snippets_in_python():
    """Jedi completions in Python include @server / @action via Jedi's results.

    The CompletionProvider delegates Python completions entirely to Jedi.
    We verify (via mock) that completing "ser" in a Python section
    returns "server" from the import.
    """
    text = dedent("""\
        from pyxle.runtime import server

        ser

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = CompletionProvider()

    mock_completion = MagicMock()
    mock_completion.name = "server"
    mock_completion.type = "function"
    mock_completion.description = "Pyxle server decorator"

    mock_script = MagicMock()
    mock_script.complete.return_value = [mock_completion]

    with patch("pyxle_langkit.completions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        items = provider.complete(doc, line=3, column=3)

    labels = [item.label for item in items]
    assert "server" in labels, (
        f"Expected 'server' in Python completions, got: {labels}"
    )


# ------------------------------------------------------------------
# Prop completions
# ------------------------------------------------------------------


def test_prop_completions_for_link():
    """Typing a prop inside <Link ...> returns Link's known props."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <Link h />;
        }
    """).strip()
    doc = _parse(text)
    provider = CompletionProvider()

    # Line 5: "    return <Link h />;"
    # Cursor after "h" — should match PROP_CONTEXT_RE.
    line_text = "    return <Link h />;"
    col = line_text.index(" h ") + 2  # after the "h"
    items = provider.complete(doc, line=5, column=col)
    labels = [item.label for item in items]
    assert "href" in labels, f"Expected 'href' in Link prop completions, got: {labels}"


# ------------------------------------------------------------------
# Import completions
# ------------------------------------------------------------------


def test_import_completions():
    """Import from 'pyxle/client' triggers component name completions."""
    text = dedent("""\
        import os

        import { } from 'pyxle/client'
        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = CompletionProvider()

    # Line 3: "import { } from 'pyxle/client'"
    # Cursor inside the braces.
    line_text = "import { } from 'pyxle/client'"
    col = line_text.index("{ ") + 2
    items = provider.complete(doc, line=3, column=col)
    labels = [item.label for item in items]
    assert "Link" in labels
    assert "Image" in labels


# ------------------------------------------------------------------
# Jedi not available
# ------------------------------------------------------------------


def test_python_completions_without_jedi(monkeypatch):
    """When Jedi is not available, Python completions return empty."""
    import pyxle_langkit.completions as comp_mod

    monkeypatch.setattr(comp_mod, "jedi", None)

    doc = _parse()
    provider = CompletionProvider()
    items = provider.complete(doc, line=1, column=5)
    assert len(items) == 0


# ------------------------------------------------------------------
# Jedi completion kind mapping
# ------------------------------------------------------------------


def test_jedi_completion_to_lsp_maps_kinds():
    """_jedi_completion_to_lsp correctly maps Jedi types to LSP kinds."""
    from pyxle_langkit.completions import _jedi_completion_to_lsp

    mock_completion = MagicMock()
    mock_completion.name = "MyClass"
    mock_completion.type = "class"
    mock_completion.description = "A test class"

    item = _jedi_completion_to_lsp(mock_completion)
    assert item.label == "MyClass"
    assert item.kind == CompletionItemKind.Class
    assert item.documentation.value == "A test class"


def test_jedi_completion_to_lsp_unknown_type():
    """Unknown Jedi type falls back to CompletionItemKind.Text."""
    from pyxle_langkit.completions import _jedi_completion_to_lsp

    mock_completion = MagicMock()
    mock_completion.name = "something"
    mock_completion.type = "unknown_type"
    mock_completion.description = ""

    item = _jedi_completion_to_lsp(mock_completion)
    assert item.kind == CompletionItemKind.Text
    # Empty description should still produce None for documentation.
    assert item.documentation is None


# ------------------------------------------------------------------
# Empty Python code
# ------------------------------------------------------------------


def test_python_empty_code_returns_empty():
    """Empty Python section returns empty completions."""
    text = "export default function Page() { return <div/>; }"
    doc = _parse(text)
    provider = CompletionProvider()
    # Line 1 is in the JSX section for an all-JSX file.
    # Try getting Python completions by forcing section manually:
    # Actually, if there's no python section, section_at_line won't return "python".
    # This tests that _complete_python handles empty virtual_code.
    result = provider._complete_python(doc, line=1, column=0)
    assert len(result) == 0


# ------------------------------------------------------------------
# Helper: _infer_loader_return_keys
# ------------------------------------------------------------------


def test_infer_loader_return_keys_no_loader():
    """No loader means no return keys."""
    from pyxle_langkit.completions import _infer_loader_return_keys

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    assert _infer_loader_return_keys(doc) == ()


def test_infer_loader_return_keys_syntax_error():
    """Syntax error in Python section returns no keys."""
    from pyxle_langkit.completions import _infer_loader_return_keys

    text = dedent("""\
        @server
        async def load(request):
            return {invalid syntax

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    assert _infer_loader_return_keys(doc) == ()


# ------------------------------------------------------------------
# Jedi exception handling
# ------------------------------------------------------------------


def test_python_completions_jedi_exception():
    """Jedi exception in complete is caught gracefully."""
    provider = CompletionProvider()

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)

    mock_script = MagicMock()
    mock_script.complete.side_effect = RuntimeError("Jedi crashed")

    with patch("pyxle_langkit.completions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        items = provider.complete(doc, line=1, column=5)

    assert len(items) == 0


# ------------------------------------------------------------------
# _map_to_virtual_line
# ------------------------------------------------------------------


def test_map_to_virtual_line():
    """_map_to_virtual_line maps pyx line to virtual line."""
    from pyxle_langkit.completions import _map_to_virtual_line

    line_numbers = (0, 1, 2, 3)
    assert _map_to_virtual_line(1, line_numbers) == 2  # pyx line 1 at index 1
    assert _map_to_virtual_line(999, line_numbers) is None


# ------------------------------------------------------------------
# _get_jsx_line_text
# ------------------------------------------------------------------


def test_get_jsx_line_text_out_of_range():
    """_get_jsx_line_text returns None for out-of-range lines."""
    from pyxle_langkit.completions import _get_jsx_line_text

    doc = _parse()
    # A line number not in the JSX section.
    result = _get_jsx_line_text(doc, 999)
    assert result is None


# ------------------------------------------------------------------
# Non-Pyxle component props return empty
# ------------------------------------------------------------------


def test_prop_completions_unknown_component():
    """Props for an unknown component return empty."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <UnknownComp f />;
        }
    """).strip()
    doc = _parse(text)
    provider = CompletionProvider()

    # Line 5: "    return <UnknownComp f />;"
    line_text = "    return <UnknownComp f />;"
    col = line_text.index(" f ") + 2
    items = provider.complete(doc, line=5, column=col)
    assert len(items) == 0
