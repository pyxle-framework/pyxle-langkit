"""Tests for the LSP server (pyxle_langkit.server) -- unit tests, not integration."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from pygls.server import LanguageServer

from pyxle_langkit.parser_adapter import TolerantParser
from pyxle_langkit.server import (
    PyxleLanguageServer,
    _extract_uri,
    _path_to_uri,
    _uri_to_path,
)


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


def _mock_workspace(text_doc_source=SAMPLE, *, raise_on_get=False):
    """Create a mock workspace that returns a TextDocument-like object."""
    mock_ws = MagicMock()
    if raise_on_get:
        mock_ws.get_text_document = MagicMock(side_effect=KeyError("not found"))
    else:
        mock_text_doc = MagicMock()
        mock_text_doc.source = text_doc_source
        mock_ws.get_text_document = MagicMock(return_value=mock_text_doc)
    return mock_ws


# ------------------------------------------------------------------
# Server creation
# ------------------------------------------------------------------


def test_server_creation():
    """PyxleLanguageServer instantiates without error."""
    server = PyxleLanguageServer()
    assert server is not None
    assert hasattr(server, "_parser")
    assert hasattr(server, "_linter")
    assert hasattr(server, "_completions")
    assert hasattr(server, "_hover")
    assert hasattr(server, "_definitions")
    assert hasattr(server, "_documents")
    assert isinstance(server._documents, dict)


def test_server_has_providers():
    """Server initializes all language feature providers."""
    from pyxle_langkit.completions import CompletionProvider
    from pyxle_langkit.definitions import DefinitionProvider
    from pyxle_langkit.hover import HoverProvider

    server = PyxleLanguageServer()
    assert isinstance(server._completions, CompletionProvider)
    assert isinstance(server._hover, HoverProvider)
    assert isinstance(server._definitions, DefinitionProvider)


# ------------------------------------------------------------------
# Document caching
# ------------------------------------------------------------------


def test_get_document_caches():
    """Documents are cached after first parse via _get_document."""
    from pyxle_langkit.server import _get_document

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    mock_ws = _mock_workspace()

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        # First call should parse and cache.
        doc1 = _get_document(server, uri)
        assert doc1 is not None
        assert uri in server._documents

        # Second call should return cached document (no workspace access needed).
        doc2 = _get_document(server, uri)
        assert doc2 is doc1


def test_get_document_returns_none_for_missing():
    """_get_document returns None when the workspace has no such document."""
    from pyxle_langkit.server import _get_document

    server = PyxleLanguageServer()
    uri = "file:///nonexistent.pyxl"

    mock_ws = _mock_workspace(raise_on_get=True)

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        doc = _get_document(server, uri)
    assert doc is None


# ------------------------------------------------------------------
# Publish diagnostics
# ------------------------------------------------------------------


def test_publish_diagnostics():
    """_publish_diagnostics calls server.publish_diagnostics."""
    from pyxle_langkit.server import _publish_diagnostics

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    mock_ws = _mock_workspace()

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics") as mock_pub,
    ):
        _publish_diagnostics(server, uri)

        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][0] == uri
        assert isinstance(call_args[0][1], list)


def test_publish_diagnostics_caches_document():
    """_publish_diagnostics stores the parsed document in the cache."""
    from pyxle_langkit.server import _publish_diagnostics

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    mock_ws = _mock_workspace()

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics"),
    ):
        _publish_diagnostics(server, uri)

    assert uri in server._documents
    doc = server._documents[uri]
    assert doc.has_python


def test_publish_diagnostics_missing_document():
    """_publish_diagnostics handles missing document gracefully."""
    from pyxle_langkit.server import _publish_diagnostics

    server = PyxleLanguageServer()
    uri = "file:///nonexistent.pyxl"

    mock_ws = _mock_workspace(raise_on_get=True)

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics") as mock_pub,
    ):
        _publish_diagnostics(server, uri)
        mock_pub.assert_not_called()


# ------------------------------------------------------------------
# Segments request
# ------------------------------------------------------------------


def test_segments_request():
    """pyxle/segments returns python and jsx code."""
    from pyxle_langkit.server import _on_segments

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    # Pre-populate the document cache.
    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = {"uri": uri}
    result = _on_segments(server, params)

    assert result is not None
    assert "python" in result
    assert "jsx" in result
    assert "code" in result["python"]
    assert "lineNumbers" in result["python"]
    assert "code" in result["jsx"]
    assert "lineNumbers" in result["jsx"]

    # Verify Python code contains the loader.
    assert "load_data" in result["python"]["code"]


def test_segments_request_no_uri():
    """pyxle/segments with no URI returns None."""
    from pyxle_langkit.server import _on_segments

    server = PyxleLanguageServer()
    result = _on_segments(server, None)
    assert result is None


def test_segments_request_missing_document():
    """pyxle/segments with unknown URI returns None."""
    from pyxle_langkit.server import _on_segments

    server = PyxleLanguageServer()

    mock_ws = _mock_workspace(raise_on_get=True)

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = _on_segments(server, {"uri": "file:///missing.pyxl"})
    assert result is None


# ------------------------------------------------------------------
# URI / Path conversion helpers
# ------------------------------------------------------------------


def test_uri_to_path():
    """_uri_to_path converts file:// URIs to Path objects."""
    path = _uri_to_path("file:///Users/test/page.pyxl")
    assert path == Path("/Users/test/page.pyxl")


def test_uri_to_path_non_file():
    """_uri_to_path returns None for non-file URIs."""
    path = _uri_to_path("https://example.com/page.pyxl")
    assert path is None


def test_path_to_uri():
    """_path_to_uri converts Path to file:// URI."""
    uri = _path_to_uri(Path("/Users/test/page.pyxl"))
    assert uri == "file:///Users/test/page.pyxl"


# ------------------------------------------------------------------
# _extract_uri helper
# ------------------------------------------------------------------


def test_extract_uri_from_dict():
    """_extract_uri extracts URI from a dict."""
    assert _extract_uri({"uri": "file:///test.pyxl"}) == "file:///test.pyxl"


def test_extract_uri_from_object():
    """_extract_uri extracts URI from an object with uri attribute."""
    params = SimpleNamespace(uri="file:///test.pyxl")
    assert _extract_uri(params) == "file:///test.pyxl"


def test_extract_uri_from_none():
    """_extract_uri returns None for None params."""
    assert _extract_uri(None) is None


def test_extract_uri_no_uri_attr():
    """_extract_uri returns None when object has no uri attribute."""
    params = SimpleNamespace(name="test")
    assert _extract_uri(params) is None


# ------------------------------------------------------------------
# Did-close clears cache
# ------------------------------------------------------------------


def test_did_close_clears_document():
    """Closing a document removes it from the cache."""
    from pyxle_langkit.server import _on_did_close

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    # Pre-populate cache.
    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE)
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri

    with patch.object(server, "publish_diagnostics") as mock_pub:
        _on_did_close(server, params)

    assert uri not in server._documents
    mock_pub.assert_called_once_with(uri, [])


# ------------------------------------------------------------------
# Linter failure in diagnostics
# ------------------------------------------------------------------


def test_publish_diagnostics_linter_failure():
    """_publish_diagnostics gracefully handles linter errors."""
    from pyxle_langkit.server import _publish_diagnostics

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    mock_ws = _mock_workspace()

    # Make the linter raise.
    server._linter = MagicMock()
    server._linter.lint = MagicMock(side_effect=RuntimeError("lint crash"))

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics") as mock_pub,
    ):
        _publish_diagnostics(server, uri)
        mock_pub.assert_called_once()


# ------------------------------------------------------------------
# Workspace index interaction
# ------------------------------------------------------------------


def test_publish_diagnostics_updates_workspace_index():
    """_publish_diagnostics updates the workspace index when present."""
    from pyxle_langkit.server import _publish_diagnostics

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    mock_ws = _mock_workspace()
    server._workspace_index = MagicMock()

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics"),
    ):
        _publish_diagnostics(server, uri)

    server._workspace_index.update.assert_called_once()


# ------------------------------------------------------------------
# Handler: _on_completion
# ------------------------------------------------------------------


def test_on_completion_returns_items():
    """_on_completion returns a CompletionList with items."""
    from pyxle_langkit.server import _on_completion

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    # Pre-populate cache with a document.
    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri
    params.position.line = 0  # 0-indexed in LSP
    params.position.character = 5

    result = _on_completion(server, params)
    assert result is not None
    assert hasattr(result, "items")
    assert result.is_incomplete is False


def test_on_completion_no_document():
    """_on_completion returns empty list when document not found."""
    from pyxle_langkit.server import _on_completion

    server = PyxleLanguageServer()

    mock_ws = _mock_workspace(raise_on_get=True)

    params = MagicMock()
    params.text_document.uri = "file:///missing.pyxl"
    params.position.line = 0
    params.position.character = 0

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = _on_completion(server, params)

    assert result.items == []


# ------------------------------------------------------------------
# Handler: _on_hover
# ------------------------------------------------------------------


def test_on_hover_returns_content():
    """_on_hover returns Hover with markdown content for @server."""
    from pyxle_langkit.server import _on_hover

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri
    # Line 2 (0-indexed) = line 3 (1-indexed) = "@server"
    params.position.line = 2
    params.position.character = 1

    result = _on_hover(server, params)
    assert result is not None
    assert "@server" in result.contents.value


def test_on_hover_returns_none():
    """_on_hover returns None for non-hoverable positions."""
    from pyxle_langkit.server import _on_hover

    server = PyxleLanguageServer()

    mock_ws = _mock_workspace(raise_on_get=True)

    params = MagicMock()
    params.text_document.uri = "file:///missing.pyxl"
    params.position.line = 0
    params.position.character = 0

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = _on_hover(server, params)

    assert result is None


def test_on_hover_no_content():
    """_on_hover returns None when hover content is None."""
    from pyxle_langkit.server import _on_hover

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri
    # Line 999 is way beyond the file.
    params.position.line = 999
    params.position.character = 0

    result = _on_hover(server, params)
    assert result is None


# ------------------------------------------------------------------
# Handler: _on_definition
# ------------------------------------------------------------------


def test_on_definition_cross_section(tmp_path):
    """_on_definition returns locations for data.key cross-section."""
    from pyxle_langkit.server import _on_definition

    server = PyxleLanguageServer()
    pyxl_file = tmp_path / "page.pyxl"
    pyxl_file.write_text(SAMPLE)
    uri = f"file://{pyxl_file}"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=pyxl_file)
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri
    # Line 9 (0-indexed) = line 10 (1-indexed) = "    return <h1>{data.title}</h1>;"
    params.position.line = 9
    line_text = "    return <h1>{data.title}</h1>;"
    params.position.character = line_text.index("title")

    result = _on_definition(server, params)
    assert result is not None
    assert len(result) > 0


def test_on_definition_no_document():
    """_on_definition returns None when document not found."""
    from pyxle_langkit.server import _on_definition

    server = PyxleLanguageServer()

    mock_ws = _mock_workspace(raise_on_get=True)

    params = MagicMock()
    params.text_document.uri = "file:///missing.pyxl"
    params.position.line = 0
    params.position.character = 0

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = _on_definition(server, params)

    assert result is None


def test_on_definition_no_results():
    """_on_definition returns None when no definitions found."""
    from pyxle_langkit.server import _on_definition

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri
    # Line at a position with no definitions.
    params.position.line = 999
    params.position.character = 0

    result = _on_definition(server, params)
    assert result is None or result == []


# ------------------------------------------------------------------
# Handler: _on_document_symbol
# ------------------------------------------------------------------


def test_on_document_symbol():
    """_on_document_symbol returns symbols for the document."""
    from pyxle_langkit.server import _on_document_symbol

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri

    result = _on_document_symbol(server, params)
    assert isinstance(result, list)


def test_on_document_symbol_no_document():
    """_on_document_symbol returns empty list when no document."""
    from pyxle_langkit.server import _on_document_symbol

    server = PyxleLanguageServer()

    mock_ws = _mock_workspace(raise_on_get=True)

    params = MagicMock()
    params.text_document.uri = "file:///missing.pyxl"

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = _on_document_symbol(server, params)

    assert result == []


# ------------------------------------------------------------------
# Handler: _on_workspace_symbol
# ------------------------------------------------------------------


def test_on_workspace_symbol_no_index():
    """_on_workspace_symbol returns empty when no workspace index."""
    from pyxle_langkit.server import _on_workspace_symbol

    server = PyxleLanguageServer()
    server._workspace_index = None

    params = MagicMock()
    params.query = "load"

    result = _on_workspace_symbol(server, params)
    assert result == []


def test_on_workspace_symbol_with_results():
    """_on_workspace_symbol returns symbols from workspace index."""
    from pyxle_langkit.server import _on_workspace_symbol

    server = PyxleLanguageServer()

    mock_symbol = MagicMock()
    mock_symbol.name = "load_data"
    mock_symbol.path = Path("/test/page.pyxl")
    mock_symbol.line = 4
    mock_symbol.kind = "loader"

    server._workspace_index = MagicMock()
    server._workspace_index.find_symbols.return_value = [mock_symbol]

    params = MagicMock()
    params.query = "load"

    result = _on_workspace_symbol(server, params)
    assert len(result) == 1
    assert result[0].name == "load_data"


# ------------------------------------------------------------------
# Handler: _on_semantic_tokens
# ------------------------------------------------------------------


def test_on_semantic_tokens():
    """_on_semantic_tokens returns encoded token data."""
    from pyxle_langkit.server import _on_semantic_tokens

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri

    result = _on_semantic_tokens(server, params)
    assert result is not None
    assert hasattr(result, "data")
    # Should have some tokens (decorator, function, params, etc.).
    assert len(result.data) > 0
    # Token data is groups of 5 integers.
    assert len(result.data) % 5 == 0


def test_on_semantic_tokens_no_document():
    """_on_semantic_tokens returns empty data for missing document."""
    from pyxle_langkit.server import _on_semantic_tokens

    server = PyxleLanguageServer()

    mock_ws = _mock_workspace(raise_on_get=True)

    params = MagicMock()
    params.text_document.uri = "file:///missing.pyxl"

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = _on_semantic_tokens(server, params)

    assert result.data == []


def test_on_semantic_tokens_no_tokens():
    """_on_semantic_tokens returns empty data for a JSX-only document."""
    from pyxle_langkit.server import _on_semantic_tokens

    server = PyxleLanguageServer()
    uri = "file:///test/jsx-only.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text("export default function Page() { return <div/>; }")
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri

    result = _on_semantic_tokens(server, params)
    assert result.data == []


# ------------------------------------------------------------------
# Handler: _on_did_open / _on_did_change / _on_did_save
# ------------------------------------------------------------------


def test_on_did_open():
    """_on_did_open publishes diagnostics."""
    from pyxle_langkit.server import _on_did_open

    server = PyxleLanguageServer()
    mock_ws = _mock_workspace()

    params = MagicMock()
    params.text_document.uri = "file:///test/page.pyxl"

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics") as mock_pub,
    ):
        _on_did_open(server, params)
        mock_pub.assert_called_once()


def test_on_did_change():
    """_on_did_change publishes diagnostics."""
    from pyxle_langkit.server import _on_did_change

    server = PyxleLanguageServer()
    mock_ws = _mock_workspace()

    params = MagicMock()
    params.text_document.uri = "file:///test/page.pyxl"

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics") as mock_pub,
    ):
        _on_did_change(server, params)
        mock_pub.assert_called_once()


def test_on_did_save():
    """_on_did_save publishes diagnostics."""
    from pyxle_langkit.server import _on_did_save

    server = PyxleLanguageServer()
    mock_ws = _mock_workspace()

    params = MagicMock()
    params.text_document.uri = "file:///test/page.pyxl"

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server, "publish_diagnostics") as mock_pub,
    ):
        _on_did_save(server, params)
        mock_pub.assert_called_once()


# ------------------------------------------------------------------
# Handler: _on_formatting
# ------------------------------------------------------------------


def test_on_formatting():
    """_on_formatting returns LSP text edits."""
    import asyncio

    from pyxle_langkit.server import _on_formatting

    server = PyxleLanguageServer()
    mock_ws = _mock_workspace()

    params = MagicMock()
    params.text_document.uri = "file:///test/page.pyxl"

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch("pyxle_langkit.formatting.shutil.which", return_value=None),
    ):
        result = asyncio.run(_on_formatting(server, params))

    # With no formatters available, returns None (no edits).
    assert result is None


def test_on_formatting_no_document():
    """_on_formatting returns None when document missing."""
    import asyncio

    from pyxle_langkit.server import _on_formatting

    server = PyxleLanguageServer()
    mock_ws = _mock_workspace(raise_on_get=True)

    params = MagicMock()
    params.text_document.uri = "file:///missing.pyxl"

    with patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws):
        result = asyncio.run(_on_formatting(server, params))

    assert result is None


# ------------------------------------------------------------------
# Handler: _on_formatting with edits returned
# ------------------------------------------------------------------


def test_on_formatting_with_edits():
    """_on_formatting returns LSP TextEdits when formatter produces changes."""
    import asyncio

    from pyxle_langkit.formatting import TextEdit
    from pyxle_langkit.server import _on_formatting

    server = PyxleLanguageServer()
    mock_ws = _mock_workspace()

    params = MagicMock()
    params.text_document.uri = "file:///test/page.pyxl"

    mock_edits = [
        TextEdit(start_line=1, end_line=2, new_text="formatted line"),
    ]

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch("pyxle_langkit.server.format_document", return_value=mock_edits),
    ):
        result = asyncio.run(_on_formatting(server, params))

    assert result is not None
    assert len(result) == 1
    assert result[0].new_text == "formatted line\n"
    assert result[0].range.start.line == 0  # 1-indexed to 0-indexed
    assert result[0].range.end.line == 1


# ------------------------------------------------------------------
# Handler: _on_completion — with CompletionList type check
# ------------------------------------------------------------------


def test_on_completion_returns_completion_list_type():
    """_on_completion returns an object of type CompletionList."""
    from lsprotocol.types import CompletionList

    from pyxle_langkit.server import _on_completion

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri
    params.position.line = 3
    params.position.character = 10

    result = _on_completion(server, params)
    assert isinstance(result, CompletionList)
    assert result.is_incomplete is False
    assert isinstance(result.items, list)


# ------------------------------------------------------------------
# Handler: _on_hover — returns Hover type
# ------------------------------------------------------------------


def test_on_hover_returns_hover_type():
    """_on_hover returns proper Hover type with MarkupContent."""
    from lsprotocol.types import Hover, MarkupContent, MarkupKind

    from pyxle_langkit.server import _on_hover

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    # Mock the hover provider to return a known string.
    server._hover = MagicMock()
    server._hover.hover.return_value = "**Test Hover**"

    params = MagicMock()
    params.text_document.uri = uri
    params.position.line = 2
    params.position.character = 1

    result = _on_hover(server, params)
    assert isinstance(result, Hover)
    assert isinstance(result.contents, MarkupContent)
    assert result.contents.kind == MarkupKind.Markdown
    assert result.contents.value == "**Test Hover**"


# ------------------------------------------------------------------
# Handler: _on_definition — returns Location list
# ------------------------------------------------------------------


def test_on_definition_returns_location_list():
    """_on_definition returns a list of Location objects."""
    from lsprotocol.types import Location

    from pyxle_langkit.server import _on_definition

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    # Mock the definitions provider to return a known result.
    mock_defn = MagicMock()
    mock_defn.path = Path("/test/page.pyxl")
    mock_defn.line = 5
    mock_defn.column = 0
    server._definitions = MagicMock()
    server._definitions.goto_definition.return_value = [mock_defn]

    params = MagicMock()
    params.text_document.uri = uri
    params.position.line = 3
    params.position.character = 10

    result = _on_definition(server, params)
    assert result is not None
    assert len(result) == 1
    assert isinstance(result[0], Location)
    assert result[0].range.start.line == 4  # 5-1 = 4 (mapped to 0-indexed)


def test_on_definition_empty_result():
    """_on_definition returns None when provider returns empty list."""
    from pyxle_langkit.server import _on_definition

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    server._definitions = MagicMock()
    server._definitions.goto_definition.return_value = []

    params = MagicMock()
    params.text_document.uri = uri
    params.position.line = 0
    params.position.character = 0

    result = _on_definition(server, params)
    assert result is None or result == []


# ------------------------------------------------------------------
# Handler: _on_document_symbol — returns symbol list
# ------------------------------------------------------------------


def test_on_document_symbol_has_symbols():
    """_on_document_symbol returns DocumentSymbol list for a valid file."""
    from pyxle_langkit.server import _on_document_symbol

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri

    result = _on_document_symbol(server, params)
    assert isinstance(result, list)
    # The SAMPLE has a loader function, so we should have at least one symbol.
    assert len(result) >= 1


# ------------------------------------------------------------------
# Handler: _on_workspace_symbol — kind mapping
# ------------------------------------------------------------------


def test_on_workspace_symbol_kind_mapping():
    """_on_workspace_symbol maps symbol kinds to LSP SymbolKind."""
    from lsprotocol.types import SymbolKind

    from pyxle_langkit.server import _on_workspace_symbol

    server = PyxleLanguageServer()

    kinds_to_test = [
        ("loader", SymbolKind.Function),
        ("action", SymbolKind.Function),
        ("function", SymbolKind.Function),
        ("class", SymbolKind.Class),
        ("default-export", SymbolKind.Interface),
        ("named-export", SymbolKind.Variable),
        ("unknown-kind", SymbolKind.Variable),  # fallback
    ]

    for kind_str, expected_lsp_kind in kinds_to_test:
        mock_symbol = MagicMock()
        mock_symbol.name = "test_symbol"
        mock_symbol.path = Path("/test/page.pyxl")
        mock_symbol.line = 1
        mock_symbol.kind = kind_str

        server._workspace_index = MagicMock()
        server._workspace_index.find_symbols.return_value = [mock_symbol]

        params = MagicMock()
        params.query = "test"

        result = _on_workspace_symbol(server, params)
        assert len(result) == 1
        assert result[0].kind == expected_lsp_kind, f"Failed for kind={kind_str}"


# ------------------------------------------------------------------
# Handler: _on_semantic_tokens — data encoding
# ------------------------------------------------------------------


def test_on_semantic_tokens_data_encoding():
    """_on_semantic_tokens encodes relative deltas per LSP spec."""
    from pyxle_langkit.server import _on_semantic_tokens

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = MagicMock()
    params.text_document.uri = uri

    result = _on_semantic_tokens(server, params)
    # Token data is groups of 5: [deltaLine, deltaStart, length, tokenType, modifiers]
    assert len(result.data) % 5 == 0
    # First token should have non-negative deltas.
    if result.data:
        assert result.data[0] >= 0  # deltaLine
        assert result.data[1] >= 0  # deltaStart
        assert result.data[2] > 0   # length


def test_on_semantic_tokens_with_mock():
    """_on_semantic_tokens handles extract_semantic_tokens producing tokens."""
    from pyxle_langkit.server import _on_semantic_tokens

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    mock_token = MagicMock()
    mock_token.line = 1
    mock_token.start_char = 0
    mock_token.length = 7
    mock_token.token_type = 0
    mock_token.modifiers = 0

    mock_token_2 = MagicMock()
    mock_token_2.line = 1
    mock_token_2.start_char = 8
    mock_token_2.length = 4
    mock_token_2.token_type = 1
    mock_token_2.modifiers = 0

    with patch("pyxle_langkit.server.extract_semantic_tokens", return_value=[mock_token, mock_token_2]):
        result = _on_semantic_tokens(server, params=MagicMock(text_document=MagicMock(uri=uri)))

    assert len(result.data) == 10  # 2 tokens x 5
    # Second token on same line: deltaLine=0, deltaStart=8
    assert result.data[5] == 0   # same line
    assert result.data[6] == 8   # delta start


# ------------------------------------------------------------------
# Handler: _on_segments — Python-only file
# ------------------------------------------------------------------


def test_segments_request_python_only():
    """pyxle/segments returns empty JSX for Python-only file."""
    from pyxle_langkit.server import _on_segments

    server = PyxleLanguageServer()
    uri = "file:///test/python-only.pyxl"

    python_only = dedent("""\
        @server
        async def loader(request):
            return {"value": 42}
    """).strip()

    parser = TolerantParser()
    doc = parser.parse_text(python_only, path=Path("/test/python-only.pyxl"))
    server._documents[uri] = doc

    result = _on_segments(server, {"uri": uri})
    assert result is not None
    assert "python" in result
    assert "jsx" in result
    assert "loader" in result["python"]["code"]


def test_segments_request_with_object_params():
    """pyxle/segments works with an object that has a uri attribute."""
    from pyxle_langkit.server import _on_segments

    server = PyxleLanguageServer()
    uri = "file:///test/page.pyxl"

    parser = TolerantParser()
    doc = parser.parse_text(SAMPLE, path=Path("/test/page.pyxl"))
    server._documents[uri] = doc

    params = SimpleNamespace(uri=uri)
    result = _on_segments(server, params)
    assert result is not None
    assert "python" in result
    assert "jsx" in result


# ------------------------------------------------------------------
# Handler: _on_did_close — nonexistent document
# ------------------------------------------------------------------


def test_did_close_nonexistent_document():
    """Closing a document that is not in the cache does not raise."""
    from pyxle_langkit.server import _on_did_close

    server = PyxleLanguageServer()
    uri = "file:///nonexistent.pyxl"

    params = MagicMock()
    params.text_document.uri = uri

    with patch.object(server, "publish_diagnostics") as mock_pub:
        _on_did_close(server, params)

    assert uri not in server._documents
    mock_pub.assert_called_once_with(uri, [])


# ------------------------------------------------------------------
# Handler: _on_initialize
# ------------------------------------------------------------------


def test_on_initialized_sets_up_workspace(tmp_path):
    """_on_initialized creates workspace index from workspace folders."""
    from pyxle_langkit.server import _on_initialized

    server = PyxleLanguageServer()

    mock_ws = MagicMock()
    mock_ws.folders = {
        "root": MagicMock(uri=f"file://{tmp_path}"),
    }
    mock_ws.root_uri = f"file://{tmp_path}"

    with (
        patch.object(LanguageServer, "workspace", new_callable=PropertyMock, return_value=mock_ws),
        patch.object(server._ts_bridge, "start", return_value=False),
    ):
        _on_initialized(server, {})

    assert server._workspace_index is not None


def test_server_has_required_providers():
    """Server instance has all provider attributes."""
    server = PyxleLanguageServer()
    assert hasattr(server, "_completions")
    assert hasattr(server, "_hover")
    assert hasattr(server, "_definitions")
    assert hasattr(server, "_ts_bridge")
    assert hasattr(server, "_linter")
