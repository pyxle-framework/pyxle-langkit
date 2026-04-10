"""AST-based semantic token provider for ``.pyx`` files.

Walks the Python AST to produce semantic tokens for decorators,
functions, parameters, classes, and built-in references, mapping all
positions back to original ``.pyx`` line numbers.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass

from pyxle_langkit.document import PyxDocument

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Token type and modifier legends
# ------------------------------------------------------------------

TOKEN_TYPES: tuple[str, ...] = (
    "namespace",     # 0
    "type",          # 1
    "class",         # 2
    "function",      # 3
    "method",        # 4
    "property",      # 5
    "variable",      # 6
    "parameter",     # 7
    "decorator",     # 8
    "keyword",       # 9
)

TOKEN_MODIFIERS: tuple[str, ...] = (
    "declaration",    # bit 0
    "definition",     # bit 1
    "readonly",       # bit 2
    "defaultLibrary", # bit 3
    "async",          # bit 4
)

# Index lookup for types.
_TYPE_INDEX: dict[str, int] = {name: i for i, name in enumerate(TOKEN_TYPES)}
_MODIFIER_INDEX: dict[str, int] = {name: i for i, name in enumerate(TOKEN_MODIFIERS)}

# Built-in function names that get the "defaultLibrary" modifier.
_BUILTINS: frozenset[str] = frozenset({
    "abs", "all", "any", "ascii", "bin", "bool", "breakpoint", "bytearray",
    "bytes", "callable", "chr", "classmethod", "compile", "complex",
    "delattr", "dict", "dir", "divmod", "enumerate", "eval", "exec",
    "filter", "float", "format", "frozenset", "getattr", "globals",
    "hasattr", "hash", "help", "hex", "id", "input", "int",
    "isinstance", "issubclass", "iter", "len", "list", "locals", "map",
    "max", "memoryview", "min", "next", "object", "oct", "open", "ord",
    "pow", "print", "property", "range", "repr", "reversed", "round",
    "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum",
    "super", "tuple", "type", "vars", "zip",
})

# Constants that get the "readonly" modifier.
_CONSTANTS: frozenset[str] = frozenset({"True", "False", "None"})


# ------------------------------------------------------------------
# Public types
# ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticToken:
    """A single semantic token with position and classification.

    Attributes
    ----------
    line:
        0-indexed line in the original ``.pyx`` file.
    start_char:
        0-indexed character offset on the line.
    length:
        Length of the token in characters.
    token_type:
        Index into ``TOKEN_TYPES``.
    modifiers:
        Bitmask of ``TOKEN_MODIFIERS``.
    """

    line: int
    start_char: int
    length: int
    token_type: int
    modifiers: int


# ------------------------------------------------------------------
# Modifier bitmask helpers
# ------------------------------------------------------------------


def _modifier_bits(*names: str) -> int:
    """Compute the modifier bitmask for the given modifier names."""
    bits = 0
    for name in names:
        idx = _MODIFIER_INDEX.get(name)
        if idx is not None:
            bits |= 1 << idx
    return bits


# ------------------------------------------------------------------
# Extraction
# ------------------------------------------------------------------


def extract_semantic_tokens(document: PyxDocument) -> tuple[SemanticToken, ...]:
    """Extract semantic tokens from the Python section of a ``.pyx`` document.

    Walks the Python AST to find decorators, function definitions,
    parameters, class definitions, built-in calls, and constants. All
    positions are mapped back to original ``.pyx`` line numbers.
    """
    if not document.has_python:
        return ()

    try:
        tree = ast.parse(document.python_code)
    except SyntaxError:
        logger.debug("Failed to parse Python section for semantic tokens")
        return ()

    tokens: list[SemanticToken] = []
    _walk_ast(tree, document, tokens)

    # Sort tokens by position for deterministic output.
    tokens.sort(key=lambda t: (t.line, t.start_char))
    return tuple(tokens)


def _walk_ast(
    tree: ast.Module,
    document: PyxDocument,
    tokens: list[SemanticToken],
) -> None:
    """Walk the AST and collect semantic tokens."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _process_function(node, document, tokens)
        elif isinstance(node, ast.ClassDef):
            _process_class(node, document, tokens)
        elif isinstance(node, ast.Call):
            _process_call(node, document, tokens)
        elif isinstance(node, ast.Name):
            _process_name(node, document, tokens)


def _process_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    document: PyxDocument,
    tokens: list[SemanticToken],
) -> None:
    """Process a function definition: decorators, name, and parameters."""
    # Decorators.
    for decorator in node.decorator_list:
        dec_line = decorator.lineno
        pyx_line = document.map_python_line(dec_line)
        if pyx_line is None:
            continue

        if isinstance(decorator, ast.Name):
            token = SemanticToken(
                line=pyx_line - 1,
                start_char=decorator.col_offset,
                length=len(decorator.id),
                token_type=_TYPE_INDEX["decorator"],
                modifiers=0,
            )
            tokens.append(token)
        elif isinstance(decorator, ast.Attribute):
            # e.g. @module.decorator — highlight the full expression
            # by highlighting just the attribute name.
            token = SemanticToken(
                line=pyx_line - 1,
                start_char=decorator.col_offset,
                length=decorator.end_col_offset - decorator.col_offset
                if decorator.end_col_offset is not None
                else len(decorator.attr),
                token_type=_TYPE_INDEX["decorator"],
                modifiers=0,
            )
            tokens.append(token)

    # Function name.
    pyx_line = document.map_python_line(node.lineno)
    if pyx_line is not None:
        is_async = isinstance(node, ast.AsyncFunctionDef)
        modifiers = _modifier_bits("definition", "async") if is_async else _modifier_bits("definition")

        # Find the column offset of the function name.
        # ast gives us the column of `def`/`async def`; the name
        # follows. We use the name directly.
        name_col = node.col_offset
        # For `async def foo`, col_offset points to `async`.
        # For `def foo`, col_offset points to `def`.
        # We need to find where `foo` starts. Use the source line.
        source_line = _get_python_source_line(document, node.lineno)
        if source_line is not None:
            name_start = source_line.find(node.name, name_col)
            if name_start != -1:
                name_col = name_start

        tokens.append(
            SemanticToken(
                line=pyx_line - 1,
                start_char=name_col,
                length=len(node.name),
                token_type=_TYPE_INDEX["function"],
                modifiers=modifiers,
            )
        )

    # Parameters.
    for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs:
        arg_line = arg.lineno
        arg_pyx_line = document.map_python_line(arg_line)
        if arg_pyx_line is not None:
            tokens.append(
                SemanticToken(
                    line=arg_pyx_line - 1,
                    start_char=arg.col_offset,
                    length=len(arg.arg),
                    token_type=_TYPE_INDEX["parameter"],
                    modifiers=_modifier_bits("definition"),
                )
            )

    if node.args.vararg is not None:
        _add_special_arg(node.args.vararg, document, tokens)
    if node.args.kwarg is not None:
        _add_special_arg(node.args.kwarg, document, tokens)


def _add_special_arg(
    arg: ast.arg,
    document: PyxDocument,
    tokens: list[SemanticToken],
) -> None:
    """Add a semantic token for *args or **kwargs."""
    pyx_line = document.map_python_line(arg.lineno)
    if pyx_line is not None:
        tokens.append(
            SemanticToken(
                line=pyx_line - 1,
                start_char=arg.col_offset,
                length=len(arg.arg),
                token_type=_TYPE_INDEX["parameter"],
                modifiers=_modifier_bits("definition"),
            )
        )


def _process_class(
    node: ast.ClassDef,
    document: PyxDocument,
    tokens: list[SemanticToken],
) -> None:
    """Process a class definition: decorators and name."""
    for decorator in node.decorator_list:
        dec_line = decorator.lineno
        pyx_line = document.map_python_line(dec_line)
        if pyx_line is None:
            continue

        if isinstance(decorator, ast.Name):
            tokens.append(
                SemanticToken(
                    line=pyx_line - 1,
                    start_char=decorator.col_offset,
                    length=len(decorator.id),
                    token_type=_TYPE_INDEX["decorator"],
                    modifiers=0,
                )
            )

    pyx_line = document.map_python_line(node.lineno)
    if pyx_line is not None:
        name_col = node.col_offset
        source_line = _get_python_source_line(document, node.lineno)
        if source_line is not None:
            name_start = source_line.find(node.name, name_col)
            if name_start != -1:
                name_col = name_start

        tokens.append(
            SemanticToken(
                line=pyx_line - 1,
                start_char=name_col,
                length=len(node.name),
                token_type=_TYPE_INDEX["class"],
                modifiers=_modifier_bits("definition"),
            )
        )


def _process_call(
    node: ast.Call,
    document: PyxDocument,
    tokens: list[SemanticToken],
) -> None:
    """Process a function call: highlight built-in function names."""
    if not isinstance(node.func, ast.Name):
        return

    name = node.func.id
    if name not in _BUILTINS:
        return

    pyx_line = document.map_python_line(node.func.lineno)
    if pyx_line is not None:
        tokens.append(
            SemanticToken(
                line=pyx_line - 1,
                start_char=node.func.col_offset,
                length=len(name),
                token_type=_TYPE_INDEX["function"],
                modifiers=_modifier_bits("defaultLibrary"),
            )
        )


def _process_name(
    node: ast.Name,
    document: PyxDocument,
    tokens: list[SemanticToken],
) -> None:
    """Process a Name node: highlight constants (True, False, None)."""
    if node.id not in _CONSTANTS:
        return

    pyx_line = document.map_python_line(node.lineno)
    if pyx_line is not None:
        tokens.append(
            SemanticToken(
                line=pyx_line - 1,
                start_char=node.col_offset,
                length=len(node.id),
                token_type=_TYPE_INDEX["variable"],
                modifiers=_modifier_bits("readonly"),
            )
        )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_python_source_line(document: PyxDocument, python_lineno: int) -> str | None:
    """Get a single line from the Python code by 1-indexed line number."""
    lines = document.python_code.splitlines()
    index = python_lineno - 1
    if 0 <= index < len(lines):
        return lines[index]
    return None
