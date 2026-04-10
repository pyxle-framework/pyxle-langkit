"""Go-to-definition provider for ``.pyx`` files.

Combines Jedi-based Python definitions with cross-section navigation
(e.g. ``data.key`` in JSX to the loader return dict in Python).
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pyxle_langkit.document import PyxDocument

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Safe jedi import
# ------------------------------------------------------------------

try:
    import jedi
except ImportError:
    jedi = None  # type: ignore[assignment]

# ------------------------------------------------------------------
# Public types
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DefinitionLocation:
    """A source location for a definition.

    Attributes
    ----------
    path:
        File path where the definition is located.
    line:
        1-indexed line number.
    column:
        0-indexed column offset.
    """

    path: Path
    line: int
    column: int


# ------------------------------------------------------------------
# Context detection
# ------------------------------------------------------------------

_DATA_KEY_RE = re.compile(r"\bdata\.(\w+)")


# ------------------------------------------------------------------
# Definition provider
# ------------------------------------------------------------------


class DefinitionProvider:
    """Provides go-to-definition for ``.pyx`` files."""

    def goto_definition(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> Sequence[DefinitionLocation]:
        """Find definitions for the symbol at the given position.

        *line* is 1-indexed, *column* is 0-indexed.
        """
        section = document.section_at_line(line)

        if section == "python":
            return self._define_python(document, line, column)
        if section == "jsx":
            return self._define_jsx(document, line, column)

        return ()

    # ------------------------------------------------------------------
    # Python definitions (via jedi)
    # ------------------------------------------------------------------

    def _define_python(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> Sequence[DefinitionLocation]:
        """Provide go-to-definition for Python sections using Jedi."""
        if jedi is None:
            return ()

        virtual_code, line_numbers = document.virtual_python_for_jedi()
        if not virtual_code.strip():
            return ()

        virtual_line = _map_to_virtual_line(line, line_numbers)
        if virtual_line is None:
            return ()

        path = str(document.path) if document.path else None
        try:
            script = jedi.Script(virtual_code, path=path)
            definitions = script.goto(virtual_line, column)
        except Exception:
            logger.debug("Jedi goto failed", exc_info=True)
            return ()

        locations: list[DefinitionLocation] = []
        for defn in definitions:
            defn_path = getattr(defn, "module_path", None)
            defn_line = getattr(defn, "line", None)
            defn_col = getattr(defn, "column", None)

            if defn_line is None:
                continue

            if defn_path is not None:
                defn_path = Path(defn_path)
            elif document.path is not None:
                defn_path = document.path
            else:
                continue

            # If the definition is within the virtual file, map back
            # to the original .pyx position.
            if document.path is not None and defn_path == document.path:
                pyx_line = _map_from_virtual_line(defn_line, line_numbers)
                if pyx_line is not None:
                    defn_line = pyx_line

            locations.append(
                DefinitionLocation(
                    path=defn_path,
                    line=defn_line,
                    column=defn_col or 0,
                )
            )

        return tuple(locations)

    # ------------------------------------------------------------------
    # JSX definitions (cross-section)
    # ------------------------------------------------------------------

    def _define_jsx(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> Sequence[DefinitionLocation]:
        """Provide go-to-definition for JSX sections.

        Handles ``data.{key}`` navigation to the loader's return dict.
        """
        line_text = _get_jsx_line_text(document, line)
        if line_text is None:
            return ()

        # Check for data.key patterns.
        for match in _DATA_KEY_RE.finditer(line_text):
            key_name = match.group(1)
            key_start = match.start(1)
            key_end = match.end(1)

            if key_start <= column < key_end:
                location = self._find_loader_dict_key(document, key_name)
                if location is not None:
                    return (location,)

        return ()

    def _find_loader_dict_key(
        self,
        document: PyxDocument,
        key_name: str,
    ) -> DefinitionLocation | None:
        """Find the position of a dict key in the loader's return statement."""
        if document.loader is None or not document.has_python:
            return None
        if document.path is None:
            return None

        try:
            tree = ast.parse(document.python_code)
        except SyntaxError:
            return None

        loader_name = document.loader.name
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name == loader_name:
                    return self._find_key_in_function(
                        document, node, key_name,
                    )

        return None

    def _find_key_in_function(
        self,
        document: PyxDocument,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        key_name: str,
    ) -> DefinitionLocation | None:
        """Locate a specific dict key in the function's return statements."""
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            if not isinstance(node.value, ast.Dict):
                continue

            for key_node in node.value.keys:
                if (
                    isinstance(key_node, ast.Constant)
                    and isinstance(key_node.value, str)
                    and key_node.value == key_name
                ):
                    python_line = key_node.lineno
                    pyx_line = document.map_python_line(python_line)
                    target_line = pyx_line or python_line

                    return DefinitionLocation(
                        path=document.path,  # type: ignore[arg-type]
                        line=target_line,
                        column=key_node.col_offset,
                    )

        return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _map_to_virtual_line(
    pyx_line: int,
    virtual_line_numbers: tuple[int, ...],
) -> int | None:
    """Map a 1-indexed .pyx line to a 1-indexed virtual Python line."""
    for virtual_idx, orig_line in enumerate(virtual_line_numbers):
        if orig_line == pyx_line:
            return virtual_idx + 1
    return None


def _map_from_virtual_line(
    virtual_line: int,
    virtual_line_numbers: tuple[int, ...],
) -> int | None:
    """Map a 1-indexed virtual Python line back to the original .pyx line."""
    index = virtual_line - 1
    if 0 <= index < len(virtual_line_numbers):
        mapped = virtual_line_numbers[index]
        return mapped if mapped != 0 else None
    return None


def _get_jsx_line_text(document: PyxDocument, pyx_line: int) -> str | None:
    """Get the text of a JSX line corresponding to a .pyx line number."""
    jsx_lines = document.jsx_code.splitlines()
    for jsx_idx, orig_line in enumerate(document.jsx_line_numbers):
        if orig_line == pyx_line and jsx_idx < len(jsx_lines):
            return jsx_lines[jsx_idx]
    return None
