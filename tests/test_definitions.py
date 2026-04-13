"""Tests for the definition provider (pyxle_langkit.definitions)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from pyxle_langkit.definitions import DefinitionProvider
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


def _parse(text: str = SAMPLE, path: Path | None = None):
    return _parser.parse_text(text, path=path)


# ------------------------------------------------------------------
# Python definition via Jedi
# ------------------------------------------------------------------


def test_python_definition_via_jedi(tmp_path):
    """Cursor on a Python symbol finds its definition via Jedi (mocked)."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        x = os.path.join("a", "b")

        export default function Page() { return <div/>; }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    # Mock jedi.Script to return a definition with path and line.
    mock_defn = MagicMock()
    mock_defn.module_path = Path("/usr/lib/python3/os.py")
    mock_defn.line = 1
    mock_defn.column = 0

    mock_script = MagicMock()
    mock_script.goto.return_value = [mock_defn]

    with patch("pyxle_langkit.definitions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        locations = provider.goto_definition(doc, line=1, column=7)

    assert len(locations) > 0
    assert locations[0].line > 0
    assert locations[0].path is not None


def test_python_definition_request_import(tmp_path):
    """Cursor on 'Request' symbol finds its definition (mocked Jedi)."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        from starlette.requests import Request

        @server
        async def loader(request: Request):
            return {"key": "value"}

        export default function Page() { return <div/>; }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    mock_defn = MagicMock()
    mock_defn.module_path = Path("/lib/starlette/requests.py")
    mock_defn.line = 42
    mock_defn.column = 0

    mock_script = MagicMock()
    mock_script.goto.return_value = [mock_defn]

    with patch("pyxle_langkit.definitions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        line_text = "from starlette.requests import Request"
        col = line_text.index("Request")
        locations = provider.goto_definition(doc, line=1, column=col)

    assert len(locations) > 0


# ------------------------------------------------------------------
# Cross-section data key definition
# ------------------------------------------------------------------


def test_cross_section_data_key(tmp_path):
    """Cursor on data.title in JSX finds 'title' key in the loader return."""
    pyxl_file = tmp_path / "page.pyxl"
    pyxl_file.write_text(SAMPLE)
    doc = _parse(SAMPLE, path=pyxl_file)
    provider = DefinitionProvider()

    # Line 10: "    return <h1>{data.title}</h1>;"
    # Cursor on "title" after "data."
    line_text = "    return <h1>{data.title}</h1>;"
    col = line_text.index("title")
    locations = provider.goto_definition(doc, line=10, column=col)
    assert len(locations) == 1

    loc = locations[0]
    assert loc.path == pyxl_file
    # The key "title" should be in the Python section's return dict.
    assert loc.line > 0


def test_cross_section_data_key_count(tmp_path):
    """Cursor on data.count in JSX finds 'count' key in the loader."""
    text = dedent("""\
        from starlette.requests import Request

        @server
        async def load_data(request: Request):
            return {"title": "Hello", "count": 42}

        import React from 'react';

        export default function Page({ data }) {
            return <span>{data.count}</span>;
        }
    """).strip()
    pyxl_file = tmp_path / "page.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    # Line 10: "    return <span>{data.count}</span>;"
    line_text = "    return <span>{data.count}</span>;"
    col = line_text.index("count")
    locations = provider.goto_definition(doc, line=10, column=col)
    assert len(locations) == 1
    assert locations[0].path == pyxl_file


# ------------------------------------------------------------------
# Unknown section returns empty
# ------------------------------------------------------------------


def test_definition_in_unknown_section():
    """Cursor in an unknown section returns empty definitions."""
    text = dedent("""\
        import os


        export default function Page() { return <div/>; }
    """).strip()
    doc = _parse(text)
    provider = DefinitionProvider()

    # Line 2 is blank between sections.
    locations = provider.goto_definition(doc, line=2, column=0)
    assert len(locations) == 0


# ------------------------------------------------------------------
# External module definition
# ------------------------------------------------------------------


def test_definition_external_module(tmp_path):
    """Jedi finds definition in an external module (mocked)."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        from pathlib import Path

        p = Path(".")

        export default function Page() { return <div/>; }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    mock_defn = MagicMock()
    mock_defn.module_path = Path("/usr/lib/python3/pathlib.py")
    mock_defn.line = 100
    mock_defn.column = 0

    mock_script = MagicMock()
    mock_script.goto.return_value = [mock_defn]

    with patch("pyxle_langkit.definitions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        line_text = "from pathlib import Path"
        col = line_text.index("Path")
        locations = provider.goto_definition(doc, line=1, column=col)

    assert len(locations) > 0
    assert locations[0].path is not None


# ------------------------------------------------------------------
# Jedi not available
# ------------------------------------------------------------------


def test_definition_without_jedi(monkeypatch):
    """When Jedi is not available, Python definitions return empty."""
    import pyxle_langkit.definitions as def_mod

    monkeypatch.setattr(def_mod, "jedi", None)

    doc = _parse()
    provider = DefinitionProvider()
    locations = provider.goto_definition(doc, line=1, column=7)
    assert len(locations) == 0


# ------------------------------------------------------------------
# JSX without data key pattern
# ------------------------------------------------------------------


def test_jsx_no_data_key_returns_empty():
    """JSX line without data.key pattern returns empty."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page() {
            return <div>Hello</div>;
        }
    """).strip()
    doc = _parse(text)
    provider = DefinitionProvider()

    # Line 5: "    return <div>Hello</div>;" — no data.key pattern.
    locations = provider.goto_definition(doc, line=5, column=15)
    assert len(locations) == 0


# ------------------------------------------------------------------
# Cross-section: no loader defined
# ------------------------------------------------------------------


def test_data_key_no_loader():
    """data.key in JSX with no @server loader returns empty."""
    text = dedent("""\
        import os

        import React from 'react';
        export default function Page({ data }) {
            return <h1>{data.title}</h1>;
        }
    """).strip()
    doc = _parse(text)
    provider = DefinitionProvider()

    line_text = "    return <h1>{data.title}</h1>;"
    col = line_text.index("title")
    locations = provider.goto_definition(doc, line=5, column=col)
    assert len(locations) == 0


# ------------------------------------------------------------------
# Cross-section: data key not in return dict
# ------------------------------------------------------------------


def test_data_key_not_in_return(tmp_path):
    """data.nonexistent in JSX returns empty when key is not in loader."""
    text = dedent("""\
        from starlette.requests import Request

        @server
        async def load_data(request: Request):
            return {"title": "Hello"}

        import React from 'react';
        export default function Page({ data }) {
            return <h1>{data.nonexistent}</h1>;
        }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    line_text = "    return <h1>{data.nonexistent}</h1>;"
    col = line_text.index("nonexistent")
    locations = provider.goto_definition(doc, line=9, column=col)
    assert len(locations) == 0


# ------------------------------------------------------------------
# Helper: _map_from_virtual_line
# ------------------------------------------------------------------


def test_map_from_virtual_line():
    """_map_from_virtual_line correctly maps back to .pyxl lines."""
    from pyxle_langkit.definitions import _map_from_virtual_line

    line_numbers = (1, 2, 3, 5, 6)

    assert _map_from_virtual_line(1, line_numbers) == 1
    assert _map_from_virtual_line(4, line_numbers) == 5
    assert _map_from_virtual_line(5, line_numbers) == 6
    assert _map_from_virtual_line(6, line_numbers) is None  # Out of range.
    assert _map_from_virtual_line(0, line_numbers) is None  # Below range.


def test_map_from_virtual_line_zero_entry():
    """_map_from_virtual_line returns None for entries mapped to 0 (injected)."""
    from pyxle_langkit.definitions import _map_from_virtual_line

    line_numbers = (0, 1, 2, 3)
    assert _map_from_virtual_line(1, line_numbers) is None  # Injected line.
    assert _map_from_virtual_line(2, line_numbers) == 1


# ------------------------------------------------------------------
# Python definition: Jedi exception handling
# ------------------------------------------------------------------


def test_python_definition_jedi_exception(tmp_path):
    """Jedi exception in goto is caught gracefully."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    mock_script = MagicMock()
    mock_script.goto.side_effect = RuntimeError("Jedi crashed")

    with patch("pyxle_langkit.definitions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        locations = provider.goto_definition(doc, line=1, column=7)

    assert len(locations) == 0


# ------------------------------------------------------------------
# Python definition: defn with None line is skipped
# ------------------------------------------------------------------


def test_python_definition_none_line_skipped(tmp_path):
    """Definitions with None line number are skipped."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        export default function Page() { return <div/>; }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    mock_defn = MagicMock()
    mock_defn.module_path = None
    mock_defn.line = None  # No line number.
    mock_defn.column = 0

    mock_script = MagicMock()
    mock_script.goto.return_value = [mock_defn]

    with patch("pyxle_langkit.definitions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        locations = provider.goto_definition(doc, line=1, column=7)

    assert len(locations) == 0


# ------------------------------------------------------------------
# Python definition: defn within virtual file maps back to pyxl
# ------------------------------------------------------------------


def test_python_definition_maps_back_to_pyxl(tmp_path):
    """Definition in the virtual file gets mapped back to .pyxl line numbers."""
    from unittest.mock import MagicMock, patch

    text = dedent("""\
        import os

        def helper():
            pass

        export default function Page() { return <div/>; }
    """).strip()
    pyxl_file = tmp_path / "test.pyxl"
    pyxl_file.write_text(text)
    doc = _parse(text, path=pyxl_file)
    provider = DefinitionProvider()

    # Simulate Jedi returning a definition within the same file.
    mock_defn = MagicMock()
    mock_defn.module_path = pyxl_file
    mock_defn.line = 3  # Virtual line 3 = "def helper()"
    mock_defn.column = 4

    mock_script = MagicMock()
    mock_script.goto.return_value = [mock_defn]

    with patch("pyxle_langkit.definitions.jedi") as mock_jedi:
        mock_jedi.Script.return_value = mock_script
        locations = provider.goto_definition(doc, line=1, column=7)

    assert len(locations) == 1


# ------------------------------------------------------------------
# _get_jsx_line_text
# ------------------------------------------------------------------


def test_get_jsx_line_text_valid():
    """_get_jsx_line_text returns the correct line text."""
    from pyxle_langkit.definitions import _get_jsx_line_text

    doc = _parse()
    # Find a JSX line.
    result = _get_jsx_line_text(doc, doc.jsx_line_numbers[0])
    assert result is not None


def test_get_jsx_line_text_not_found():
    """_get_jsx_line_text returns None for non-JSX lines."""
    from pyxle_langkit.definitions import _get_jsx_line_text

    doc = _parse()
    result = _get_jsx_line_text(doc, 999)
    assert result is None


# ------------------------------------------------------------------
# Cross-section: no path on document
# ------------------------------------------------------------------


def test_data_key_no_path():
    """data.key with no path on document returns empty."""
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
    # Parse without providing a path.
    doc = _parse(text, path=None)
    provider = DefinitionProvider()

    line_text = "    return <h1>{data.title}</h1>;"
    col = line_text.index("title")
    locations = provider.goto_definition(doc, line=9, column=col)
    assert len(locations) == 0
