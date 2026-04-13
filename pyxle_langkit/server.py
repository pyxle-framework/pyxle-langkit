"""Language Server Protocol (LSP) server for Pyxle ``.pyxl`` files.

All language intelligence is provided here — the VS Code extension is a
thin client that just connects to this server.  Features include
diagnostics, completions (via Jedi), hover, go-to-definition, document
symbols, workspace symbols, formatting, and semantic tokens.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from lsprotocol.types import (
    TEXT_DOCUMENT_COMPLETION,
    TEXT_DOCUMENT_DEFINITION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    TEXT_DOCUMENT_DOCUMENT_SYMBOL,
    TEXT_DOCUMENT_FORMATTING,
    TEXT_DOCUMENT_HOVER,
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    WORKSPACE_SYMBOL,
    CompletionItem,
    CompletionItemKind,
    CompletionList,
    CompletionOptions,
    CompletionParams,
    DefinitionParams,
    Diagnostic,
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    DocumentFormattingParams,
    DocumentSymbolParams,
    Hover,
    HoverParams,
    Location,
    MarkupContent,
    MarkupKind,
    Position,
    Range,
    SemanticTokens,
    SemanticTokensLegend,
    SemanticTokensParams,
    TextEdit as LspTextEdit,
    WorkspaceSymbolParams,
)
from lsprotocol.types import (
    DocumentSymbol as LspDocumentSymbol,
)
from lsprotocol.types import (
    WorkspaceSymbol as LspWorkspaceSymbol,
)
from pygls.server import LanguageServer
from pygls.workspace import TextDocument

from pyxle import __version__ as PYXLE_VERSION

from .completions import CompletionProvider
from .definitions import DefinitionProvider
from .diagnostics import (
    lint_issues_to_lsp_diagnostics,
    parser_diagnostics_to_lsp,
)
from .document import PyxDocument
from .formatting import format_document
from .hover import HoverProvider
from .linter import PyxLinter
from .parser_adapter import TolerantParser
from .semantic_tokens import (
    TOKEN_MODIFIERS,
    TOKEN_TYPES,
    extract_semantic_tokens,
)
from .symbols import (
    document_symbols_to_lsp,
    extract_document_symbols,
)
from .ts_bridge import TypeScriptBridge
from .workspace import WorkspaceIndex

logger = logging.getLogger(__name__)

# Map TypeScript completion kinds to LSP CompletionItemKind.
_TS_COMPLETION_KIND: dict[str, CompletionItemKind] = {
    "keyword": CompletionItemKind.Keyword,
    "function": CompletionItemKind.Function,
    "method": CompletionItemKind.Method,
    "property": CompletionItemKind.Property,
    "var": CompletionItemKind.Variable,
    "let": CompletionItemKind.Variable,
    "const": CompletionItemKind.Variable,
    "local var": CompletionItemKind.Variable,
    "class": CompletionItemKind.Class,
    "interface": CompletionItemKind.Interface,
    "type": CompletionItemKind.Interface,
    "enum": CompletionItemKind.Enum,
    "enum member": CompletionItemKind.EnumMember,
    "module": CompletionItemKind.Module,
    "alias": CompletionItemKind.Reference,
    "string": CompletionItemKind.Value,
    "JSX attribute": CompletionItemKind.Property,
}


# ------------------------------------------------------------------
# Server
# ------------------------------------------------------------------


class PyxleLanguageServer(LanguageServer):
    """Pyxle language server — all IDE intelligence in one process."""

    def __init__(self) -> None:
        super().__init__("pyxle-langserver", PYXLE_VERSION)
        self._parser = TolerantParser()
        self._linter = PyxLinter()
        self._completions = CompletionProvider()
        self._hover = HoverProvider()
        self._definitions = DefinitionProvider()
        self._ts_bridge = TypeScriptBridge()
        self._documents: dict[str, PyxDocument] = {}
        self._workspace_index: WorkspaceIndex | None = None
        self._project_root: Path | None = None


_server = PyxleLanguageServer()


# ------------------------------------------------------------------
# Lifecycle
# ------------------------------------------------------------------


@_server.feature("initialized")
def _on_initialized(server: PyxleLanguageServer, params: object) -> None:
    """Handle the ``initialized`` notification from the client.

    Sets up the workspace index for workspace-wide symbol search.
    The index is populated lazily as files are opened — no background
    scan to avoid event-loop lifecycle issues.
    """
    root_path: Path | None = None
    try:
        folders = server.workspace.folders
        if folders:
            first_uri = next(iter(folders.values())).uri
            root_path = _uri_to_path(first_uri)
    except Exception:
        pass

    if root_path is None:
        try:
            root_uri = getattr(server.workspace, "root_uri", None)
            if root_uri:
                root_path = _uri_to_path(root_uri)
        except Exception:
            pass

    if root_path and root_path.is_dir():
        server._workspace_index = WorkspaceIndex(root_path)
        server._project_root = root_path

    # Start the TypeScript language service for JSX intelligence.
    server._ts_bridge.start(project_root=root_path)

    logger.info("Pyxle language server initialized (root=%s)", root_path)


# ------------------------------------------------------------------
# Document lifecycle → diagnostics
# ------------------------------------------------------------------


@_server.feature(TEXT_DOCUMENT_DID_OPEN)
def _on_did_open(
    server: PyxleLanguageServer, params: DidOpenTextDocumentParams
) -> None:
    _publish_diagnostics(server, params.text_document.uri)


@_server.feature(TEXT_DOCUMENT_DID_CHANGE)
def _on_did_change(
    server: PyxleLanguageServer, params: DidChangeTextDocumentParams
) -> None:
    _publish_diagnostics(server, params.text_document.uri)


@_server.feature(TEXT_DOCUMENT_DID_SAVE)
def _on_did_save(
    server: PyxleLanguageServer, params: DidSaveTextDocumentParams
) -> None:
    _publish_diagnostics(server, params.text_document.uri)


@_server.feature(TEXT_DOCUMENT_DID_CLOSE)
def _on_did_close(
    server: PyxleLanguageServer, params: DidCloseTextDocumentParams
) -> None:
    uri = params.text_document.uri
    server._documents.pop(uri, None)
    server.publish_diagnostics(uri, [])


# ------------------------------------------------------------------
# Completions
# ------------------------------------------------------------------


@_server.feature(
    TEXT_DOCUMENT_COMPLETION,
    CompletionOptions(trigger_characters=[".", "(", ",", "<", "'", '"', "/"]),
)
def _on_completion(
    server: PyxleLanguageServer, params: CompletionParams
) -> CompletionList:
    document = _get_document(server, params.text_document.uri)
    if document is None:
        return CompletionList(is_incomplete=False, items=[])

    line = params.position.line + 1      # LSP is 0-indexed → 1-indexed
    column = params.position.character   # already 0-indexed

    # Python completions via jedi + Pyxle-specific JSX completions.
    items = list(server._completions.complete(document, line, column))

    # TypeScript completions for JSX sections.
    section = document.section_at_line(line)
    if section == "jsx" and server._ts_bridge.is_running and document.path:
        ts_items = server._ts_bridge.completions(
            document.path, params.position.line, column,
        )
        for ts_item in ts_items:
            items.append(CompletionItem(
                label=ts_item.label,
                kind=_TS_COMPLETION_KIND.get(ts_item.kind, CompletionItemKind.Text),
                sort_text=ts_item.sort_text,
                insert_text=ts_item.insert_text,
            ))

    return CompletionList(is_incomplete=False, items=items)


# ------------------------------------------------------------------
# Hover
# ------------------------------------------------------------------


@_server.feature(TEXT_DOCUMENT_HOVER)
def _on_hover(
    server: PyxleLanguageServer, params: HoverParams
) -> Hover | None:
    document = _get_document(server, params.text_document.uri)
    if document is None:
        return None

    line = params.position.line + 1
    column = params.position.character

    # Try Pyxle-specific hover first (decorators, components).
    content = server._hover.hover(document, line, column)

    # For JSX sections, also try TypeScript hover if Pyxle hover is empty.
    if content is None and document.section_at_line(line) == "jsx":
        if server._ts_bridge.is_running and document.path:
            ts_info = server._ts_bridge.quick_info(
                document.path, params.position.line, column,
            )
            if ts_info and ts_info.display:
                parts = [f"```typescript\n{ts_info.display}\n```"]
                if ts_info.documentation:
                    parts.append(ts_info.documentation)
                content = "\n\n".join(parts)

    if content is None:
        return None

    return Hover(
        contents=MarkupContent(kind=MarkupKind.Markdown, value=content),
    )


# ------------------------------------------------------------------
# Go-to-definition
# ------------------------------------------------------------------


@_server.feature(TEXT_DOCUMENT_DEFINITION)
def _on_definition(
    server: PyxleLanguageServer, params: DefinitionParams
) -> list[Location] | None:
    document = _get_document(server, params.text_document.uri)
    if document is None:
        return None

    line = params.position.line + 1
    column = params.position.character

    # Try Pyxle-specific definitions first (jedi + cross-section data.key).
    definitions = server._definitions.goto_definition(document, line, column)

    locations: list[Location] = []
    for defn in definitions:
        uri = _path_to_uri(defn.path)
        pos = Position(line=max(defn.line - 1, 0), character=defn.column)
        locations.append(
            Location(uri=uri, range=Range(start=pos, end=pos))
        )

    # For JSX sections, also try TypeScript definitions.
    if not locations and document.section_at_line(line) == "jsx":
        if server._ts_bridge.is_running and document.path:
            ts_defs = server._ts_bridge.definition(
                document.path, params.position.line, column,
            )
            for ts_def in ts_defs:
                def_path = Path(ts_def.file)
                # If the definition points to a .pyxl file, use the .pyxl URI.
                # If it points to a .js/.jsx/.ts file, use that directly.
                uri = _path_to_uri(def_path)
                pos = Position(line=ts_def.line, character=ts_def.character)
                locations.append(
                    Location(uri=uri, range=Range(start=pos, end=pos))
                )
    return locations


# ------------------------------------------------------------------
# Document symbols
# ------------------------------------------------------------------


@_server.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def _on_document_symbol(
    server: PyxleLanguageServer, params: DocumentSymbolParams
) -> list[LspDocumentSymbol]:
    document = _get_document(server, params.text_document.uri)
    if document is None:
        return []

    symbols = extract_document_symbols(document)
    return document_symbols_to_lsp(symbols)


# ------------------------------------------------------------------
# Workspace symbols
# ------------------------------------------------------------------


@_server.feature(WORKSPACE_SYMBOL)
def _on_workspace_symbol(
    server: PyxleLanguageServer, params: WorkspaceSymbolParams
) -> list[LspWorkspaceSymbol]:
    if server._workspace_index is None:
        return []

    ws_symbols = server._workspace_index.find_symbols(params.query)
    results: list[LspWorkspaceSymbol] = []
    for ws in ws_symbols:
        from lsprotocol.types import SymbolKind

        uri = _path_to_uri(ws.path)
        pos = Position(line=max(ws.line - 1, 0), character=0)
        location = Location(uri=uri, range=Range(start=pos, end=pos))

        # Map kind string to LSP SymbolKind.
        kind_map = {
            "loader": SymbolKind.Function,
            "action": SymbolKind.Function,
            "function": SymbolKind.Function,
            "class": SymbolKind.Class,
            "default-export": SymbolKind.Interface,
            "named-export": SymbolKind.Variable,
        }
        kind = kind_map.get(ws.kind, SymbolKind.Variable)

        results.append(
            LspWorkspaceSymbol(name=ws.name, kind=kind, location=location)
        )
    return results


# ------------------------------------------------------------------
# Formatting
# ------------------------------------------------------------------


@_server.feature(TEXT_DOCUMENT_FORMATTING)
async def _on_formatting(
    server: PyxleLanguageServer, params: DocumentFormattingParams
) -> list[LspTextEdit] | None:
    text_doc = _get_text_document(server, params.text_document.uri)
    if text_doc is None:
        return None

    edits = await format_document(text_doc.source)
    if not edits:
        return None

    lsp_edits: list[LspTextEdit] = []
    for edit in edits:
        start = Position(line=edit.start_line - 1, character=0)
        end = Position(line=edit.end_line - 1, character=0)
        lsp_edits.append(
            LspTextEdit(range=Range(start=start, end=end), new_text=edit.new_text + "\n")
        )
    return lsp_edits


# ------------------------------------------------------------------
# Semantic tokens
# ------------------------------------------------------------------


@_server.feature(
    TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL,
    SemanticTokensLegend(
        token_types=list(TOKEN_TYPES),
        token_modifiers=list(TOKEN_MODIFIERS),
    ),
)
def _on_semantic_tokens(
    server: PyxleLanguageServer, params: SemanticTokensParams
) -> SemanticTokens:
    document = _get_document(server, params.text_document.uri)
    if document is None:
        return SemanticTokens(data=[])

    tokens = extract_semantic_tokens(document)
    if not tokens:
        return SemanticTokens(data=[])

    # Encode tokens as relative deltas per the LSP spec.
    data: list[int] = []
    prev_line = 0
    prev_start = 0

    for token in sorted(tokens, key=lambda t: (t.line, t.start_char)):
        delta_line = token.line - prev_line
        delta_start = token.start_char if delta_line > 0 else token.start_char - prev_start
        data.extend([
            delta_line,
            delta_start,
            token.length,
            token.token_type,
            token.modifiers,
        ])
        prev_line = token.line
        prev_start = token.start_char

    return SemanticTokens(data=data)


# ------------------------------------------------------------------
# Custom: pyxle/segments (backward compatibility)
# ------------------------------------------------------------------


@_server.feature("pyxle/segments")
def _on_segments(
    server: PyxleLanguageServer, params: object | None
) -> dict[str, object] | None:
    """Return Python/JSX segments for any extension features that need them."""
    uri = _extract_uri(params)
    if not uri:
        return None

    document = _get_document(server, uri)
    if document is None:
        return None

    return {
        "python": {
            "code": document.python_code,
            "lineNumbers": list(document.python_line_numbers),
        },
        "jsx": {
            "code": document.jsx_code,
            "lineNumbers": list(document.jsx_line_numbers),
        },
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _publish_diagnostics(server: PyxleLanguageServer, uri: str) -> None:
    """Parse, lint, and publish diagnostics for a document."""
    text_doc = _get_text_document(server, uri)
    if text_doc is None:
        return

    # Parse.
    file_path = _uri_to_path(uri)
    document = server._parser.parse_text(text_doc.source, path=file_path)
    server._documents[uri] = document

    # Update workspace index.
    if server._workspace_index is not None and document.path is not None:
        server._workspace_index.update(document.path, document)

    # Feed JSX content to the TypeScript bridge for completions/hover/definitions.
    if document.has_jsx and server._ts_bridge.is_running and file_path:
        _update_ts_bridge(server, document, file_path)

    # Collect diagnostics from parser.
    diagnostics: list[Diagnostic] = list(
        parser_diagnostics_to_lsp(document.diagnostics)
    )

    # Collect diagnostics from linter.
    try:
        issues = server._linter.lint(document)
        diagnostics.extend(lint_issues_to_lsp_diagnostics(issues))
    except Exception:
        logger.debug("Linting failed for %s", uri, exc_info=True)

    server.publish_diagnostics(uri, diagnostics)


def _update_ts_bridge(
    server: PyxleLanguageServer,
    document: PyxDocument,
    file_path: Path,
) -> None:
    """Build a virtual TSX document and feed it to the TypeScript bridge.

    The virtual document preserves line alignment: Python lines become
    empty lines so that JSX line N in the virtual file corresponds to
    line N in the original ``.pyxl`` file.  This eliminates the need
    for position mapping — positions pass through as-is.
    """
    jsx_lines = document.jsx_code.splitlines()
    jsx_line_numbers = document.jsx_line_numbers

    if not jsx_lines or not jsx_line_numbers:
        return

    # Find the total line count in the original file.
    all_line_numbers = list(document.python_line_numbers) + list(jsx_line_numbers)
    total_lines = max(all_line_numbers) if all_line_numbers else 0

    # Build padded content where each line maps to the original .pyxl line.
    virtual_lines: list[str] = [""] * total_lines
    for i, jsx_line in enumerate(jsx_lines):
        if i < len(jsx_line_numbers):
            orig_line = jsx_line_numbers[i]
            if 0 < orig_line <= total_lines:
                virtual_lines[orig_line - 1] = jsx_line

    virtual_content = "\n".join(virtual_lines)
    server._ts_bridge.update_file(
        file_path,
        virtual_content,
        project_root=server._project_root,
    )


def _get_document(server: PyxleLanguageServer, uri: str) -> PyxDocument | None:
    """Get a cached PyxDocument, parsing if necessary."""
    if uri in server._documents:
        return server._documents[uri]

    text_doc = _get_text_document(server, uri)
    if text_doc is None:
        return None

    document = server._parser.parse_text(text_doc.source, path=_uri_to_path(uri))
    server._documents[uri] = document
    return document


def _get_text_document(server: PyxleLanguageServer, uri: str) -> TextDocument | None:
    """Get a TextDocument from the workspace."""
    try:
        return server.workspace.get_text_document(uri)
    except KeyError:
        return None


def _uri_to_path(uri: str) -> Path | None:
    """Convert a file:// URI to a Path."""
    if uri.startswith("file://"):
        return Path(uri[7:])
    return None


def _path_to_uri(path: Path) -> str:
    """Convert a Path to a file:// URI."""
    return f"file://{path}"


def _extract_uri(params: object | None) -> str | None:
    """Extract a URI from a custom request params object."""
    if params is None:
        return None
    if isinstance(params, dict):
        return params.get("uri")
    return getattr(params, "uri", None)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    """Start the language server."""
    parser = argparse.ArgumentParser(description="Pyxle language server")
    parser.add_argument(
        "--tcp",
        nargs=2,
        metavar=("HOST", "PORT"),
        help="Run over TCP instead of stdio.",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Force stdio mode (default).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.tcp:
        host, port = args.tcp
        _server.start_tcp(host, int(port))
    else:
        _server.start_io()


if __name__ == "__main__":  # pragma: no cover
    main()
