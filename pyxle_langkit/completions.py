"""Completion provider for ``.pyx`` files.

Combines Jedi-based Python completions with Pyxle-specific component
and import completions for JSX sections.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from typing import Sequence

from lsprotocol.types import (
    CompletionItem,
    CompletionItemKind,
    InsertTextFormat,
    MarkupContent,
    MarkupKind,
)

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
# Pyxle component definitions
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ComponentDef:
    """Definition of a built-in Pyxle component for completion."""

    name: str
    props: tuple[str, ...]
    doc: str
    is_container: bool = False


_PYXLE_COMPONENTS: tuple[_ComponentDef, ...] = (
    _ComponentDef(
        name="Link",
        props=("href", "prefetch", "replace", "scroll"),
        doc="Client-side navigation link. Prefetches on hover by default.",
    ),
    _ComponentDef(
        name="Script",
        props=("src", "strategy", "async", "defer", "module", "noModule"),
        doc="Managed script tag with loading strategy control.",
    ),
    _ComponentDef(
        name="Image",
        props=("src", "width", "height", "alt", "priority", "lazy"),
        doc="Optimised image component with automatic sizing.",
    ),
    _ComponentDef(
        name="Head",
        props=(),
        doc="Container for ``<head>`` meta elements.",
        is_container=True,
    ),
    _ComponentDef(
        name="Slot",
        props=("name",),
        doc="Named slot for layout composition.",
    ),
    _ComponentDef(
        name="ClientOnly",
        props=(),
        doc="Renders children only on the client (skipped during SSR).",
        is_container=True,
    ),
    _ComponentDef(
        name="Form",
        props=("action", "method"),
        doc="Enhanced form with server action integration.",
    ),
)

_PYXLE_COMPONENT_NAMES = frozenset(c.name for c in _PYXLE_COMPONENTS)
_PYXLE_COMPONENT_MAP = {c.name: c for c in _PYXLE_COMPONENTS}

_IMPORT_SOURCE = "pyxle/client"

# ------------------------------------------------------------------
# Tag context detection
# ------------------------------------------------------------------

_TAG_OPEN_RE = re.compile(r"<\s*([A-Z]\w*)?$")
_PROP_CONTEXT_RE = re.compile(r"<\s*([A-Z]\w+)\b[^>]*\s+(\w*)$")
_DATA_DOT_RE = re.compile(r"\bdata\.(\w*)$")
_IMPORT_RE = re.compile(
    r"""import\s*\{[^}]*$|from\s+['"]pyxle/client['"]\s*$""",
)

# ------------------------------------------------------------------
# Jedi completion kind mapping
# ------------------------------------------------------------------

_JEDI_KIND_MAP: dict[str, CompletionItemKind] = {
    "module": CompletionItemKind.Module,
    "class": CompletionItemKind.Class,
    "instance": CompletionItemKind.Variable,
    "function": CompletionItemKind.Function,
    "param": CompletionItemKind.Variable,
    "path": CompletionItemKind.File,
    "keyword": CompletionItemKind.Keyword,
    "property": CompletionItemKind.Property,
    "statement": CompletionItemKind.Variable,
}


# ------------------------------------------------------------------
# Completion provider
# ------------------------------------------------------------------


class CompletionProvider:
    """Provides completions for Python and JSX sections of ``.pyx`` files."""

    def complete(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> Sequence[CompletionItem]:
        """Provide completions at the given position in a ``.pyx`` file.

        *line* is 1-indexed (matches LSP convention after +1 adjustment).
        *column* is 0-indexed.
        """
        section = document.section_at_line(line)

        if section == "python":
            return self._complete_python(document, line, column)
        if section == "jsx":
            return self._complete_jsx(document, line, column)

        return ()

    # ------------------------------------------------------------------
    # Python completions (via jedi)
    # ------------------------------------------------------------------

    def _complete_python(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> Sequence[CompletionItem]:
        """Provide Python completions using Jedi."""
        if jedi is None:
            logger.debug("Jedi not available; skipping Python completions")
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
            completions = script.complete(virtual_line, column)
        except Exception:
            logger.debug("Jedi completion failed", exc_info=True)
            return ()

        return tuple(
            _jedi_completion_to_lsp(c) for c in completions
        )

    # ------------------------------------------------------------------
    # JSX completions (Pyxle-specific)
    # ------------------------------------------------------------------

    def _complete_jsx(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> Sequence[CompletionItem]:
        """Provide JSX completions for Pyxle components, props, and data."""
        # Get the current line text from JSX source.
        line_text = _get_jsx_line_text(document, line)
        if line_text is None:
            return ()

        prefix = line_text[:column] if column <= len(line_text) else line_text
        items: list[CompletionItem] = []

        # 1. Component tag completions (after '<').
        tag_match = _TAG_OPEN_RE.search(prefix)
        if tag_match is not None:
            items.extend(self._complete_components(tag_match.group(1) or ""))
            return tuple(items)

        # 2. Prop completions (inside an open tag).
        prop_match = _PROP_CONTEXT_RE.search(prefix)
        if prop_match is not None:
            component_name = prop_match.group(1)
            prop_prefix = prop_match.group(2)
            items.extend(self._complete_props(component_name, prop_prefix))
            return tuple(items)

        # 3. data.{key} completions.
        data_match = _DATA_DOT_RE.search(prefix)
        if data_match is not None:
            items.extend(self._complete_data_keys(document, data_match.group(1)))
            return tuple(items)

        # 4. Import completions.
        if _IMPORT_RE.search(prefix):
            items.extend(self._complete_imports(""))
            return tuple(items)

        return tuple(items)

    def _complete_components(self, prefix: str) -> Sequence[CompletionItem]:
        """Provide Pyxle component name completions."""
        items: list[CompletionItem] = []
        for comp in _PYXLE_COMPONENTS:
            if comp.name.startswith(prefix):
                snippet = (
                    f"{comp.name}>${{0}}</{comp.name}>"
                    if comp.is_container
                    else f"{comp.name} ${{0}}/>"
                )
                items.append(
                    CompletionItem(
                        label=comp.name,
                        kind=CompletionItemKind.Class,
                        detail=f"pyxle/client — {comp.doc}",
                        insert_text=snippet,
                        insert_text_format=InsertTextFormat.Snippet,
                    )
                )
        return items

    def _complete_props(
        self,
        component_name: str,
        prop_prefix: str,
    ) -> Sequence[CompletionItem]:
        """Provide prop completions for a Pyxle component."""
        comp = _PYXLE_COMPONENT_MAP.get(component_name)
        if comp is None:
            return ()

        items: list[CompletionItem] = []
        for prop in comp.props:
            if prop.startswith(prop_prefix):
                items.append(
                    CompletionItem(
                        label=prop,
                        kind=CompletionItemKind.Property,
                        detail=f"{component_name} prop",
                        insert_text=f'{prop}={{${{0}}}}',
                        insert_text_format=InsertTextFormat.Snippet,
                    )
                )
        return items

    def _complete_data_keys(
        self,
        document: PyxDocument,
        prefix: str,
    ) -> Sequence[CompletionItem]:
        """Provide ``data.{key}`` completions from the loader return dict."""
        keys = _infer_loader_return_keys(document)
        items: list[CompletionItem] = []
        for key in keys:
            if key.startswith(prefix):
                items.append(
                    CompletionItem(
                        label=key,
                        kind=CompletionItemKind.Property,
                        detail="loader data",
                    )
                )
        return items

    def _complete_imports(self, prefix: str) -> Sequence[CompletionItem]:
        """Provide import completions for ``pyxle/client``."""
        items: list[CompletionItem] = []
        for name in sorted(_PYXLE_COMPONENT_NAMES):
            if name.startswith(prefix):
                items.append(
                    CompletionItem(
                        label=name,
                        kind=CompletionItemKind.Class,
                        detail=f"import from {_IMPORT_SOURCE}",
                    )
                )
        return items


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _map_to_virtual_line(
    pyx_line: int,
    virtual_line_numbers: tuple[int, ...],
) -> int | None:
    """Map a 1-indexed .pyx line to a 1-indexed virtual Python line.

    Searches the line map for the first entry matching *pyx_line*.
    Returns ``None`` if no mapping exists.
    """
    for virtual_idx, orig_line in enumerate(virtual_line_numbers):
        if orig_line == pyx_line:
            return virtual_idx + 1
    return None


def _get_jsx_line_text(document: PyxDocument, pyx_line: int) -> str | None:
    """Get the text of a JSX line corresponding to a .pyx line number.

    Returns ``None`` if the line is not in the JSX section.
    """
    jsx_lines = document.jsx_code.splitlines()
    for jsx_idx, orig_line in enumerate(document.jsx_line_numbers):
        if orig_line == pyx_line and jsx_idx < len(jsx_lines):
            return jsx_lines[jsx_idx]
    return None


def _jedi_completion_to_lsp(completion: object) -> CompletionItem:
    """Convert a Jedi completion to an LSP ``CompletionItem``."""
    name = getattr(completion, "name", "")
    type_str = getattr(completion, "type", "")
    description = getattr(completion, "description", "")

    kind = _JEDI_KIND_MAP.get(type_str, CompletionItemKind.Text)

    doc: MarkupContent | None = None
    if description:
        doc = MarkupContent(kind=MarkupKind.PlainText, value=description)

    return CompletionItem(
        label=name,
        kind=kind,
        detail=type_str,
        documentation=doc,
    )


def _infer_loader_return_keys(document: PyxDocument) -> tuple[str, ...]:
    """Infer the dict keys returned by the ``@server`` loader function.

    Parses the Python AST looking for the loader function and inspects
    its return statement(s). If the return value is a dict literal,
    extracts the string keys.
    """
    if document.loader is None or not document.has_python:
        return ()

    try:
        tree = ast.parse(document.python_code)
    except SyntaxError:
        return ()

    loader_name = document.loader.name
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name == loader_name:
                return _extract_return_dict_keys(node)

    return ()


def _extract_return_dict_keys(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Extract string keys from dict return statements in a function."""
    keys: list[str] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.append(key.value)
    return tuple(dict.fromkeys(keys))  # Deduplicate, preserve order.
