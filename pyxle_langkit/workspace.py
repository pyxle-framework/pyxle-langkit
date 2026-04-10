"""Workspace index for .pyx files.

Maintains an in-memory index of all ``.pyx`` documents in a project,
supporting incremental updates and cross-file symbol search.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .document import PyxDocument
from .parser_adapter import TolerantParser

logger = logging.getLogger(__name__)

_SYMBOL_KIND_FUNCTION = "function"
_SYMBOL_KIND_ASYNC_FUNCTION = "async function"
_SYMBOL_KIND_CLASS = "class"
_SYMBOL_KIND_VARIABLE = "variable"
_SYMBOL_KIND_LOADER = "loader"
_SYMBOL_KIND_ACTION = "action"


@dataclass(frozen=True, slots=True)
class WorkspaceSymbol:
    """A named symbol found in a ``.pyx`` document."""

    name: str
    kind: str
    path: Path
    line: int
    detail: str | None


class WorkspaceIndex:
    """In-memory index of all ``.pyx`` documents in a project workspace.

    Not frozen -- this is mutable state that tracks the current set of
    parsed documents and supports incremental updates as files change.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._documents: dict[Path, PyxDocument] = {}
        self._parser = TolerantParser()

    @property
    def root(self) -> Path:
        """The workspace root directory."""
        return self._root

    def scan(self) -> None:
        """Walk ``root/pages/`` recursively and parse all ``.pyx`` files.

        Replaces the entire index with fresh parse results. Files that
        fail to parse are still indexed (with diagnostics on the
        document).
        """
        pages_dir = self._root / "pages"
        if not pages_dir.is_dir():
            logger.debug("No pages/ directory found under %s", self._root)
            self._documents.clear()
            return

        new_docs: dict[Path, PyxDocument] = {}
        for pyx_path in sorted(pages_dir.rglob("*.pyx")):
            doc = self._parser.parse(pyx_path)
            new_docs[pyx_path] = doc

        self._documents = new_docs
        logger.debug(
            "Scanned %d .pyx files under %s", len(self._documents), pages_dir
        )

    def update(self, path: Path, document: PyxDocument) -> None:
        """Update or add a single document in the index."""
        self._documents[path] = document

    def remove(self, path: Path) -> None:
        """Remove a document from the index.

        No-op if the path is not in the index.
        """
        self._documents.pop(path, None)

    def get(self, path: Path) -> PyxDocument | None:
        """Return the cached document for *path*, or ``None``."""
        return self._documents.get(path)

    def all_documents(self) -> Mapping[Path, PyxDocument]:
        """Return a read-only view of all cached documents."""
        return self._documents

    def find_symbols(self, query: str) -> Sequence[WorkspaceSymbol]:
        """Search all indexed documents for symbols matching *query*.

        Performs a case-insensitive substring match on symbol names.
        Returns symbols sorted by (path, line).
        """
        query_lower = query.lower()
        results: list[WorkspaceSymbol] = []

        for path, doc in self._documents.items():
            for symbol in _extract_symbols(doc, path):
                if query_lower in symbol.name.lower():
                    results.append(symbol)

        results.sort(key=lambda s: (s.path, s.line))
        return results


def _extract_symbols(doc: PyxDocument, path: Path) -> Sequence[WorkspaceSymbol]:
    """Extract all named symbols from a document's Python section.

    Extracts top-level functions, classes, and variable assignments.
    Loader and action functions are tagged with their decorator kind.
    """
    symbols: list[WorkspaceSymbol] = []

    # Tag loader and action function names for kind detection.
    loader_name = doc.loader.name if doc.loader else None
    action_names = frozenset(a.name for a in doc.actions)

    if not doc.python_code.strip():
        return symbols

    try:
        tree = ast.parse(doc.python_code, mode="exec")
    except SyntaxError:
        return symbols

    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            line = _map_to_original(node.lineno, doc.python_line_numbers)
            if node.name == loader_name:
                kind = _SYMBOL_KIND_LOADER
                detail = "@server loader"
            elif node.name in action_names:
                kind = _SYMBOL_KIND_ACTION
                detail = "@action"
            else:
                kind = _SYMBOL_KIND_ASYNC_FUNCTION
                detail = None
            symbols.append(WorkspaceSymbol(
                name=node.name, kind=kind, path=path,
                line=line, detail=detail,
            ))

        elif isinstance(node, ast.FunctionDef):
            line = _map_to_original(node.lineno, doc.python_line_numbers)
            symbols.append(WorkspaceSymbol(
                name=node.name, kind=_SYMBOL_KIND_FUNCTION, path=path,
                line=line, detail=None,
            ))

        elif isinstance(node, ast.ClassDef):
            line = _map_to_original(node.lineno, doc.python_line_numbers)
            symbols.append(WorkspaceSymbol(
                name=node.name, kind=_SYMBOL_KIND_CLASS, path=path,
                line=line, detail=None,
            ))

        elif isinstance(node, ast.Assign):
            line = _map_to_original(node.lineno, doc.python_line_numbers)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.append(WorkspaceSymbol(
                        name=target.id, kind=_SYMBOL_KIND_VARIABLE,
                        path=path, line=line, detail=None,
                    ))

        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            line = _map_to_original(node.lineno, doc.python_line_numbers)
            symbols.append(WorkspaceSymbol(
                name=node.target.id, kind=_SYMBOL_KIND_VARIABLE,
                path=path, line=line, detail=None,
            ))

    return symbols


def _map_to_original(
    virtual_line: int, line_numbers: tuple[int, ...],
) -> int:
    """Map a 1-indexed line in the virtual Python code to the original .pyx line."""
    if not line_numbers or virtual_line < 1:
        return virtual_line
    index = min(virtual_line - 1, len(line_numbers) - 1)
    return line_numbers[index]
