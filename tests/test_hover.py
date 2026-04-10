"""Tests for the hover provider (pyxle_langkit.hover)."""

from __future__ import annotations

from textwrap import dedent

from pyxle_langkit.hover import HoverProvider
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
# Pyxle decorator hover
# ------------------------------------------------------------------


def test_hover_server_decorator():
    """Hovering @server returns Pyxle loader documentation."""
    doc = _parse()
    provider = HoverProvider()

    # Line 3 in the SAMPLE: "@server"
    result = provider.hover(doc, line=3, column=1)
    assert result is not None
    assert "@server" in result
    assert "Pyxle Loader" in result
    assert "data loader" in result


def test_hover_action_decorator():
    """Hovering @action returns Pyxle action documentation."""
    text = dedent("""\
        from starlette.requests import Request

        @action
        async def submit_form(request: Request):
            return {"success": True}

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Line 3: "@action"
    result = provider.hover(doc, line=3, column=1)
    assert result is not None
    assert "@action" in result
    assert "Server Action" in result


# ------------------------------------------------------------------
# HEAD variable hover
# ------------------------------------------------------------------


def test_hover_head_variable():
    """Hovering 'Head' component in JSX returns Head component docs."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <Head><title>Hi</title></Head>;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Line 5: "    return <Head><title>Hi</title></Head>;"
    # "Head" starts at col 12.
    line_text = "    return <Head><title>Hi</title></Head>;"
    col = line_text.index("Head")  # column of 'H' in '<Head>'
    result = provider.hover(doc, line=5, column=col)
    assert result is not None
    assert "Head" in result
    assert "Document Head" in result


# ------------------------------------------------------------------
# JSX component hover
# ------------------------------------------------------------------


def test_hover_jsx_component():
    """Hovering Link, Script, Image, etc. returns component docs."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <Link href="/about">About</Link>;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Line 5: "    return <Link href="/about">About</Link>;"
    line_text = '    return <Link href="/about">About</Link>;'
    col = line_text.index("Link")
    result = provider.hover(doc, line=5, column=col)
    assert result is not None
    assert "Link" in result
    assert "Client-Side Navigation" in result


def test_hover_image_component():
    """Hovering Image in JSX returns optimised image docs."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <Image src="/hero.jpg" alt="Hero" />;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    line_text = '    return <Image src="/hero.jpg" alt="Hero" />;'
    col = line_text.index("Image")
    result = provider.hover(doc, line=5, column=col)
    assert result is not None
    assert "Image" in result
    assert "Optimised" in result


# ------------------------------------------------------------------
# Python symbol hover via Jedi
# ------------------------------------------------------------------


def test_hover_python_symbol_via_jedi():
    """Hovering a Python name returns Jedi documentation (mocked)."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Mock jedi.Script to return a help name with docstring.
    mock_name = MagicMock()
    mock_name.get_signatures.return_value = []
    mock_name.module_name = "os"
    mock_name.docstring.return_value = "OS routines for Mac, NT, or Posix."

    mock_script = MagicMock()
    mock_script.help.return_value = [mock_name]

    with patch("pyxle_langkit.hover.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        result = provider.hover(doc, line=1, column=7)

    assert result is not None
    assert "os" in result.lower()


# ------------------------------------------------------------------
# Empty / None returns
# ------------------------------------------------------------------


def test_hover_empty_returns_none():
    """Cursor in a blank area returns None."""
    text = dedent("""\
        import os


        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Line 2 is a blank line between sections.
    result = provider.hover(doc, line=2, column=0)
    assert result is None


def test_hover_unknown_section_returns_none():
    """Cursor in unknown section returns None."""
    doc = _parse()
    provider = HoverProvider()

    # Line well beyond the file length.
    result = provider.hover(doc, line=999, column=0)
    assert result is None


# ------------------------------------------------------------------
# Data hover in JSX
# ------------------------------------------------------------------


def test_hover_data_in_jsx():
    """Hovering 'data' in JSX shows loader info and inferred keys."""
    doc = _parse()
    provider = HoverProvider()

    # Find the JSX line with "data.title" in the SAMPLE.
    # Line 10: "    return <h1>{data.title}</h1>;"
    line_text = "    return <h1>{data.title}</h1>;"
    data_col = line_text.index("data")
    result = provider.hover(doc, line=10, column=data_col)
    assert result is not None
    assert "data" in result
    assert "load_data" in result
    # Should show inferred keys.
    assert "title" in result
    assert "count" in result


def test_hover_data_no_loader():
    """Hovering 'data' when no @server loader returns appropriate message."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page({ data }) {
            return <h1>{data.title}</h1>;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Line 5: "    return <h1>{data.title}</h1>;"
    line_text = "    return <h1>{data.title}</h1>;"
    col = line_text.index("data")
    result = provider.hover(doc, line=5, column=col)
    assert result is not None
    assert "No" in result and "loader" in result


# ------------------------------------------------------------------
# Jedi not available
# ------------------------------------------------------------------


def test_hover_without_jedi(monkeypatch):
    """When Jedi is not available, Python hover returns None for non-decorator lines."""
    import pyxle_langkit.hover as hover_mod

    monkeypatch.setattr(hover_mod, "jedi", None)

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # "import os" — without Jedi, can't hover python symbols.
    result = provider.hover(doc, line=1, column=7)
    assert result is None


# ------------------------------------------------------------------
# JSX hover: cursor not on component name
# ------------------------------------------------------------------


def test_hover_jsx_cursor_not_on_tag():
    """Cursor on JSX text (not a component tag) returns None."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <div>hello world</div>;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Line 5: "    return <div>hello world</div>;"
    # "div" is lowercase, so _COMPONENT_TAG_RE won't match it.
    # Cursor on "hello" text.
    result = provider.hover(doc, line=5, column=20)
    assert result is None


# ------------------------------------------------------------------
# Jedi hover: with signature
# ------------------------------------------------------------------


def test_hover_jedi_with_signature():
    """Jedi hover includes function signature when available."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    mock_sig = MagicMock()
    mock_sig.to_string.return_value = "def join(a: str, *p: str) -> str"

    mock_name = MagicMock()
    mock_name.get_signatures.return_value = [mock_sig]
    mock_name.module_name = "os.path"
    mock_name.docstring.return_value = "Join path components."

    mock_script = MagicMock()
    mock_script.help.return_value = [mock_name]

    with patch("pyxle_langkit.hover.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        result = provider.hover(doc, line=1, column=7)

    assert result is not None
    assert "join" in result
    assert "os.path" in result
    assert "Join path" in result


def test_hover_jedi_no_results():
    """Jedi hover returns None when no names are found."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    mock_script = MagicMock()
    mock_script.help.return_value = []

    with patch("pyxle_langkit.hover.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        result = provider.hover(doc, line=1, column=7)

    assert result is None


def test_hover_jedi_exception():
    """Jedi exception in help is caught gracefully."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    mock_script = MagicMock()
    mock_script.help.side_effect = RuntimeError("Jedi crashed")

    with patch("pyxle_langkit.hover.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        result = provider.hover(doc, line=1, column=7)

    assert result is None


# ------------------------------------------------------------------
# Data hover edge case: loader but can't infer return keys
# ------------------------------------------------------------------


def test_hover_data_loader_no_return_keys():
    """Hovering data with loader that doesn't return a dict shows partial info."""
    text = dedent("""\
        from starlette.requests import Request

        @server
        async def load_data(request: Request):
            result = compute()
            return result

        import React from 'react';
        export default function Page({ data }) {
            return <h1>{data.title}</h1>;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    line_text = "    return <h1>{data.title}</h1>;"
    col = line_text.index("data")
    result = provider.hover(doc, line=10, column=col)
    assert result is not None
    assert "load_data" in result
    assert "could not be inferred" in result


# ------------------------------------------------------------------
# JSX hover: component outside cursor range
# ------------------------------------------------------------------


def test_hover_jsx_component_cursor_after_name():
    """Cursor after the component name (on props) returns None for the component."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <Link href="/about">About</Link>;
        }
    """).strip()
    doc = _parse(text)
    provider = HoverProvider()

    # Cursor on "href" which is after the "Link" name.
    line_text = '    return <Link href="/about">About</Link>;'
    col = line_text.index("href")
    result = provider.hover(doc, line=5, column=col)
    # Should be None since cursor is on "href" not "Link".
    assert result is None


# ------------------------------------------------------------------
# Helper: _get_python_line_text
# ------------------------------------------------------------------


def test_get_python_line_text():
    """_get_python_line_text returns correct line text."""
    from pyxle_langkit.hover import _get_python_line_text

    doc = _parse()
    # First Python line.
    result = _get_python_line_text(doc, doc.python_line_numbers[0])
    assert result is not None


def test_get_python_line_text_not_found():
    """_get_python_line_text returns None for non-Python lines."""
    from pyxle_langkit.hover import _get_python_line_text

    doc = _parse()
    result = _get_python_line_text(doc, 999)
    assert result is None
