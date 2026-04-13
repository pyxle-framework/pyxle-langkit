"""Parsed document model for .pyxl files.

Wraps the raw ``PyxParseResult`` from the core compiler into a richer,
IDE-oriented model with line mapping, section detection, and Jedi-ready
Python virtual code generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pyxle.compiler.parser import (
    ActionDetails,
    LoaderDetails,
    PyxDiagnostic,
)
from pyxle.compiler.writers import ensure_action_import, ensure_server_import

_REQUEST_IMPORT = "from starlette.requests import Request"
_REQUEST_ANNOTATION_RE = re.compile(
    r"^(\s*async\s+def\s+\w+\s*\(\s*request)\b(?!\s*:)",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class PyxDocument:
    """Immutable representation of a parsed ``.pyxl`` file.

    Contains all metadata extracted by the compiler parser, plus helper
    methods for line mapping and IDE integration.
    """

    path: Path | None
    source: str
    python_code: str
    jsx_code: str
    python_line_numbers: tuple[int, ...]
    jsx_line_numbers: tuple[int, ...]
    loader: LoaderDetails | None
    actions: tuple[ActionDetails, ...]
    head_elements: tuple[str, ...]
    head_is_dynamic: bool
    diagnostics: tuple[PyxDiagnostic, ...]
    script_declarations: tuple[dict, ...]
    image_declarations: tuple[dict, ...]
    head_jsx_blocks: tuple[str, ...]

    # ------------------------------------------------------------------
    # Line mapping
    # ------------------------------------------------------------------

    def map_python_line(self, virtual_line: int) -> int | None:
        """Map a 1-indexed virtual Python line to the original .pyxl line.

        Returns ``None`` if *virtual_line* is out of range.
        """
        if virtual_line < 1 or not self.python_line_numbers:
            return None
        index = virtual_line - 1
        if index >= len(self.python_line_numbers):
            return None
        return self.python_line_numbers[index]

    def map_jsx_line(self, virtual_line: int) -> int | None:
        """Map a 1-indexed virtual JSX line to the original .pyxl line.

        Returns ``None`` if *virtual_line* is out of range.
        """
        if virtual_line < 1 or not self.jsx_line_numbers:
            return None
        index = virtual_line - 1
        if index >= len(self.jsx_line_numbers):
            return None
        return self.jsx_line_numbers[index]

    # ------------------------------------------------------------------
    # Section detection
    # ------------------------------------------------------------------

    def section_at_line(self, original_line: int) -> Literal["python", "jsx", "unknown"]:
        """Determine which section a 1-indexed original .pyxl line belongs to.

        Returns ``"python"`` if the line appears in the Python line map,
        ``"jsx"`` if it appears in the JSX line map, or ``"unknown"``
        otherwise (blank lines between sections, or out-of-range).
        """
        if original_line in self.python_line_numbers:
            return "python"
        if original_line in self.jsx_line_numbers:
            return "jsx"
        return "unknown"

    # ------------------------------------------------------------------
    # Jedi integration
    # ------------------------------------------------------------------

    def virtual_python_for_jedi(self) -> tuple[str, tuple[int, ...]]:
        """Produce Python code with injected imports suitable for Jedi analysis.

        Injects ``from starlette.requests import Request`` at the top,
        adds ``from pyxle.runtime import server`` / ``action`` when
        loader or actions are present, and annotates bare ``request``
        parameters with ``: Request`` type hints.

        Returns a tuple of (code, line_numbers) where *line_numbers*
        maps each virtual line to an original .pyxl line number. Injected
        lines use ``0`` as their line number.
        """
        code = self.python_code
        line_numbers = list(self.python_line_numbers)

        if not code.strip():
            return (code, tuple(line_numbers))

        # Step 1: Inject runtime imports if needed.
        # Each ensure_*_import call may insert one line. We track the
        # original line numbers through a mutable list, inserting 0 at
        # positions corresponding to injected lines.
        has_loader = self.loader is not None
        has_actions = bool(self.actions)

        if has_loader and has_actions:
            code = ensure_action_import(code)
            code, server_pos = ensure_server_import(
                code, return_insert_position=True,
            )
        elif has_loader:
            code, _ = ensure_server_import(
                code, return_insert_position=True,
            )
        elif has_actions:
            code = ensure_action_import(code)

        # Step 2: Inject the Request import at the very top.
        need_request_import = _REQUEST_IMPORT not in code
        if need_request_import:
            code = _REQUEST_IMPORT + "\n" + code

        # Step 3: Build the line number map BEFORE annotation (which
        # only mutates line content, not line count). We walk the final
        # lines and match each against the original code to identify
        # which are injected (mapped to 0) and which come from the
        # original source.
        final_lines = code.split("\n")
        if final_lines and final_lines[-1] == "":
            final_lines = final_lines[:-1]

        original_lines = self.python_code.split("\n")
        if original_lines and original_lines[-1] == "":
            original_lines = original_lines[:-1]

        final_line_numbers: list[int] = []
        orig_idx = 0
        for line in final_lines:
            if orig_idx < len(original_lines) and line == original_lines[orig_idx]:
                if orig_idx < len(self.python_line_numbers):
                    final_line_numbers.append(self.python_line_numbers[orig_idx])
                else:
                    final_line_numbers.append(0)
                orig_idx += 1
            else:
                final_line_numbers.append(0)

        # Step 4: Annotate ``request`` parameters with ``: Request``.
        # This changes line content but not line count, so the line
        # number map stays valid.
        code = _REQUEST_ANNOTATION_RE.sub(r"\1: Request", code)

        return (code, tuple(final_line_numbers))

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def has_python(self) -> bool:
        """Whether the document contains a non-empty Python section."""
        return bool(self.python_code.strip())

    @property
    def has_jsx(self) -> bool:
        """Whether the document contains a non-empty JSX section."""
        return bool(self.jsx_code.strip())
