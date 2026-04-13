"""Document and workspace symbol providers.

Extracts structural symbols (loaders, actions, functions, classes,
exports) from parsed ``.pyxl`` documents and converts them to LSP
``DocumentSymbol`` objects.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Sequence

from lsprotocol.types import (
    DocumentSymbol as LspDocumentSymbol,
    Position,
    Range,
    SymbolKind,
)

from pyxle_langkit.document import PyxDocument

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Internal symbol model
# ------------------------------------------------------------------

_LSP_KIND_MAP: dict[str, SymbolKind] = {
    "loader": SymbolKind.Function,
    "action": SymbolKind.Function,
    "function": SymbolKind.Function,
    "class": SymbolKind.Class,
    "default-export": SymbolKind.Interface,
    "named-export": SymbolKind.Variable,
}


@dataclass(frozen=True, slots=True)
class DocumentSymbol:
    """A structural symbol extracted from a ``.pyxl`` document.

    Attributes
    ----------
    name:
        Symbol name (function name, class name, export identifier).
    kind:
        One of ``"loader"``, ``"action"``, ``"function"``, ``"class"``,
        ``"default-export"``, ``"named-export"``.
    line:
        1-indexed line number in the original ``.pyxl`` file.
    detail:
        Optional human-readable detail string (e.g. ``"async loader"``).
    """

    name: str
    kind: str
    line: int
    detail: str | None = None


# ------------------------------------------------------------------
# Symbol extraction
# ------------------------------------------------------------------


def extract_document_symbols(document: PyxDocument) -> tuple[DocumentSymbol, ...]:
    """Extract symbols from a parsed ``.pyxl`` document.

    Finds ``@server`` loaders, ``@action`` functions, top-level Python
    functions and classes, and JSX export patterns.
    """
    symbols: list[DocumentSymbol] = []

    # --- Loader ---
    if document.loader is not None:
        loader = document.loader
        pyx_line = document.map_python_line(loader.line_number)
        detail = "async loader" if loader.is_async else "loader"
        symbols.append(
            DocumentSymbol(
                name=loader.name,
                kind="loader",
                line=pyx_line or loader.line_number,
                detail=detail,
            )
        )

    # --- Actions ---
    for action in document.actions:
        pyx_line = document.map_python_line(action.line_number)
        detail = "async action" if action.is_async else "action"
        symbols.append(
            DocumentSymbol(
                name=action.name,
                kind="action",
                line=pyx_line or action.line_number,
                detail=detail,
            )
        )

    # --- Top-level functions and classes from Python AST ---
    _extract_python_ast_symbols(document, symbols)

    # --- JSX exports (heuristic) ---
    _extract_jsx_export_symbols(document, symbols)

    return tuple(symbols)


def _extract_python_ast_symbols(
    document: PyxDocument,
    symbols: list[DocumentSymbol],
) -> None:
    """Extract top-level functions and classes from the Python AST.

    Skips functions already captured as loader or action.
    """
    if not document.has_python:
        return

    try:
        tree = ast.parse(document.python_code)
    except SyntaxError:
        logger.debug("Failed to parse Python section for symbol extraction")
        return

    known_names = {s.name for s in symbols}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in known_names:
                continue
            pyx_line = document.map_python_line(node.lineno)
            detail = "async function" if isinstance(node, ast.AsyncFunctionDef) else None
            symbols.append(
                DocumentSymbol(
                    name=node.name,
                    kind="function",
                    line=pyx_line or node.lineno,
                    detail=detail,
                )
            )
        elif isinstance(node, ast.ClassDef):
            if node.name in known_names:
                continue
            pyx_line = document.map_python_line(node.lineno)
            symbols.append(
                DocumentSymbol(
                    name=node.name,
                    kind="class",
                    line=pyx_line or node.lineno,
                )
            )


def _extract_jsx_export_symbols(
    document: PyxDocument,
    symbols: list[DocumentSymbol],
) -> None:
    """Extract export declarations from JSX code via heuristic parsing.

    Looks for ``export default`` and ``export function/const`` patterns.
    Skips lines that are inside template literals (backtick strings) to
    avoid treating code snippets in template literals as real exports.
    """
    if not document.has_jsx:
        return

    jsx_lines = document.jsx_code.splitlines()
    in_template_literal = False

    for i, raw_line in enumerate(jsx_lines, start=1):
        # Track template literal state: count unescaped backticks.
        # A line with an odd number of backticks toggles the state.
        backtick_count = _count_unescaped_backticks(raw_line)
        if backtick_count % 2 == 1:
            in_template_literal = not in_template_literal

        if in_template_literal:
            continue

        line = raw_line.strip()
        pyx_line = document.map_jsx_line(i)
        target_line = pyx_line or i

        if line.startswith("export default"):
            name = _parse_export_name(line, "export default")
            symbols.append(
                DocumentSymbol(
                    name=name or "default",
                    kind="default-export",
                    line=target_line,
                    detail="default export",
                )
            )
        elif line.startswith("export ") and not line.startswith("export default"):
            name = _parse_export_name(line, "export")
            if name:
                symbols.append(
                    DocumentSymbol(
                        name=name,
                        kind="named-export",
                        line=target_line,
                        detail="named export",
                    )
                )


def _count_unescaped_backticks(line: str) -> int:
    """Count backticks in a line that are not preceded by a backslash."""
    count = 0
    prev = ""
    for ch in line:
        if ch == "`" and prev != "\\":
            count += 1
        prev = ch
    return count


def _parse_export_name(line: str, prefix: str) -> str | None:
    """Extract the identifier following an export keyword.

    Handles ``export default function Foo``, ``export const Bar``,
    ``export default class Baz``, etc.
    """
    rest = line[len(prefix) :].strip()

    for keyword in ("function ", "async function ", "class ", "const ", "let ", "var "):
        if rest.startswith(keyword):
            rest = rest[len(keyword) :]
            break

    # Extract identifier (stops at non-identifier characters).
    name_chars: list[str] = []
    for ch in rest:
        if ch.isalnum() or ch == "_" or ch == "$":
            name_chars.append(ch)
        else:
            break

    return "".join(name_chars) if name_chars else None


# ------------------------------------------------------------------
# LSP conversion
# ------------------------------------------------------------------


def document_symbol_to_lsp(symbol: DocumentSymbol) -> LspDocumentSymbol:
    """Convert a ``DocumentSymbol`` to an LSP ``DocumentSymbol``.

    Maps internal kind strings to LSP ``SymbolKind`` constants.
    """
    lsp_kind = _LSP_KIND_MAP.get(symbol.kind, SymbolKind.Variable)
    line = max(symbol.line - 1, 0)  # Convert to 0-indexed.

    symbol_range = Range(
        start=Position(line=line, character=0),
        end=Position(line=line, character=0),
    )

    return LspDocumentSymbol(
        name=symbol.name,
        kind=lsp_kind,
        range=symbol_range,
        selection_range=symbol_range,
        detail=symbol.detail,
    )


def document_symbols_to_lsp(
    symbols: Sequence[DocumentSymbol],
) -> list[LspDocumentSymbol]:
    """Convert a sequence of ``DocumentSymbol`` to LSP ``DocumentSymbol`` list."""
    return [document_symbol_to_lsp(s) for s in symbols]
