"""Tests for pyxle_langkit.symbols — symbol extraction and LSP conversion."""

from __future__ import annotations

from textwrap import dedent

from lsprotocol.types import SymbolKind

from pyxle_langkit.document import PyxDocument
from pyxle_langkit.parser_adapter import TolerantParser
from pyxle_langkit.symbols import (
    DocumentSymbol,
    document_symbol_to_lsp,
    document_symbols_to_lsp,
    extract_document_symbols,
)


def _make_doc(text: str) -> PyxDocument:
    return TolerantParser().parse_text(text)


# ------------------------------------------------------------------
# Loader extraction
# ------------------------------------------------------------------


class TestExtractLoaderSymbol:
    """@server loader function is found as a symbol."""

    def test_loader_found(self) -> None:
        text = dedent("""\
            @server
            async def loader(request):
                return {"ok": True}

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        loaders = [s for s in symbols if s.kind == "loader"]
        assert len(loaders) == 1
        assert loaders[0].name == "loader"
        assert "loader" in (loaders[0].detail or "")

    def test_async_loader_detail(self) -> None:
        text = dedent("""\
            @server
            async def loader(request):
                return {"ok": True}

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        loaders = [s for s in symbols if s.kind == "loader"]
        assert loaders[0].detail == "async loader"


# ------------------------------------------------------------------
# Action extraction
# ------------------------------------------------------------------


class TestExtractActionSymbols:
    """@action functions are found as symbols."""

    def test_action_found(self) -> None:
        text = dedent("""\
            @action
            async def submit(request):
                pass

            @action
            async def delete(request):
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        actions = [s for s in symbols if s.kind == "action"]
        assert len(actions) == 2
        names = {s.name for s in actions}
        assert "submit" in names
        assert "delete" in names

    def test_action_detail(self) -> None:
        text = dedent("""\
            @action
            async def submit(request):
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        actions = [s for s in symbols if s.kind == "action"]
        assert "action" in (actions[0].detail or "")


# ------------------------------------------------------------------
# Function and class extraction
# ------------------------------------------------------------------


class TestExtractFunctionAndClass:
    """Top-level Python functions and classes are found."""

    def test_function_found(self) -> None:
        text = dedent("""\
            def helper():
                pass

            class MyModel:
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        functions = [s for s in symbols if s.kind == "function"]
        classes = [s for s in symbols if s.kind == "class"]
        assert any(s.name == "helper" for s in functions)
        assert any(s.name == "MyModel" for s in classes)

    def test_loader_not_duplicated_as_function(self) -> None:
        text = dedent("""\
            @server
            async def loader(request):
                return {"ok": True}

            def helper():
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        # "loader" should appear once as a loader, not also as a function.
        loader_syms = [s for s in symbols if s.name == "loader"]
        assert len(loader_syms) == 1
        assert loader_syms[0].kind == "loader"


# ------------------------------------------------------------------
# JSX export extraction
# ------------------------------------------------------------------


class TestExtractJsxExports:
    """Default and named exports in JSX are found."""

    def test_default_export_found(self) -> None:
        text = dedent("""\
            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        default_exports = [s for s in symbols if s.kind == "default-export"]
        assert len(default_exports) >= 1

    def test_named_export_found(self) -> None:
        text = dedent("""\
            ---

            export function Header() {
                return <header>Header</header>;
            }

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        named_exports = [s for s in symbols if s.kind == "named-export"]
        assert len(named_exports) >= 1
        assert any(s.name == "Header" for s in named_exports)


# ------------------------------------------------------------------
# LSP conversion
# ------------------------------------------------------------------


class TestDocumentSymbolToLsp:
    """DocumentSymbol converts correctly to LSP DocumentSymbol."""

    def test_function_kind(self) -> None:
        sym = DocumentSymbol(name="my_func", kind="function", line=5)
        lsp = document_symbol_to_lsp(sym)
        assert lsp.name == "my_func"
        assert lsp.kind == SymbolKind.Function
        # Line 5 -> 0-indexed line 4.
        assert lsp.range.start.line == 4

    def test_class_kind(self) -> None:
        sym = DocumentSymbol(name="MyClass", kind="class", line=10)
        lsp = document_symbol_to_lsp(sym)
        assert lsp.kind == SymbolKind.Class

    def test_loader_kind(self) -> None:
        sym = DocumentSymbol(
            name="loader", kind="loader", line=3, detail="async loader"
        )
        lsp = document_symbol_to_lsp(sym)
        assert lsp.kind == SymbolKind.Function
        assert lsp.detail == "async loader"

    def test_default_export_kind(self) -> None:
        sym = DocumentSymbol(
            name="Page", kind="default-export", line=12, detail="default export"
        )
        lsp = document_symbol_to_lsp(sym)
        assert lsp.kind == SymbolKind.Interface

    def test_named_export_kind(self) -> None:
        sym = DocumentSymbol(
            name="Header", kind="named-export", line=8, detail="named export"
        )
        lsp = document_symbol_to_lsp(sym)
        assert lsp.kind == SymbolKind.Variable

    def test_line_zero_handled(self) -> None:
        sym = DocumentSymbol(name="x", kind="function", line=0)
        lsp = document_symbol_to_lsp(sym)
        assert lsp.range.start.line == 0

    def test_unknown_kind_defaults_to_variable(self) -> None:
        sym = DocumentSymbol(name="x", kind="unknown-kind", line=1)
        lsp = document_symbol_to_lsp(sym)
        assert lsp.kind == SymbolKind.Variable


class TestDocumentSymbolsToLsp:
    """Batch conversion of DocumentSymbol list to LSP list."""

    def test_batch_conversion(self) -> None:
        syms = [
            DocumentSymbol(name="a", kind="function", line=1),
            DocumentSymbol(name="b", kind="class", line=5),
        ]
        lsp_list = document_symbols_to_lsp(syms)
        assert len(lsp_list) == 2

    def test_empty_list(self) -> None:
        assert document_symbols_to_lsp([]) == []


# ------------------------------------------------------------------
# Empty document
# ------------------------------------------------------------------


class TestEmptyDocumentSymbols:
    """Empty document produces no symbols."""

    def test_no_symbols(self) -> None:
        doc = _make_doc("")
        symbols = extract_document_symbols(doc)
        assert symbols == ()

    def test_whitespace_only(self) -> None:
        doc = _make_doc("   \n\n  ")
        symbols = extract_document_symbols(doc)
        assert symbols == ()


class TestExtractPythonAstEdgeCases:
    """Edge cases in Python AST symbol extraction."""

    def test_syntax_error_no_crash(self) -> None:
        doc = _make_doc("def broken(\n    pass\n")
        symbols = extract_document_symbols(doc)
        # Should not crash; may return empty or partial symbols.
        assert isinstance(symbols, tuple)

    def test_async_function_detail(self) -> None:
        text = dedent("""\
            async def my_helper():
                pass
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        async_funcs = [s for s in symbols if s.name == "my_helper"]
        assert len(async_funcs) >= 1
        assert async_funcs[0].detail == "async function"

    def test_class_no_detail(self) -> None:
        text = dedent("""\
            class MyModel:
                pass
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        classes = [s for s in symbols if s.name == "MyModel"]
        assert len(classes) >= 1
        assert classes[0].detail is None


class TestExportNameParsing:
    """_parse_export_name handles various export patterns."""

    def test_export_const(self) -> None:
        text = dedent("""\
            ---

            export const Settings = () => <div>Settings</div>;
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        named = [s for s in symbols if s.kind == "named-export"]
        assert any(s.name == "Settings" for s in named)

    def test_export_default_class(self) -> None:
        text = dedent("""\
            ---

            export default class App {}
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        default = [s for s in symbols if s.kind == "default-export"]
        assert len(default) >= 1

    def test_export_default_no_name(self) -> None:
        text = dedent("""\
            ---

            export default () => <div>Anonymous</div>;
        """)
        doc = _make_doc(text)
        symbols = extract_document_symbols(doc)
        default = [s for s in symbols if s.kind == "default-export"]
        assert len(default) >= 1
        # Falls back to "default" when no name can be parsed.
        assert default[0].name == "default"
