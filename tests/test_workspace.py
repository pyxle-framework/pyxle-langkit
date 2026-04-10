"""Tests for pyxle_langkit.workspace — WorkspaceIndex."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from pyxle_langkit.document import PyxDocument
from pyxle_langkit.parser_adapter import TolerantParser
from pyxle_langkit.workspace import WorkspaceIndex, WorkspaceSymbol


class TestScanFindsPyxFiles:
    """WorkspaceIndex.scan discovers .pyx files under pages/."""

    def test_scan_finds_files(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        docs = index.all_documents()
        assert len(docs) == 2

    def test_scan_finds_expected_paths(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        paths = {p.name for p in index.all_documents()}
        assert "index.pyx" in paths
        assert "about.pyx" in paths

    def test_scan_parses_documents(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        for doc in index.all_documents().values():
            assert isinstance(doc, PyxDocument)


class TestUpdateAndGet:
    """WorkspaceIndex.update stores and .get retrieves a document."""

    def test_update_then_get(self, tmp_path: Path, sample_pyx_text: str) -> None:
        index = WorkspaceIndex(tmp_path)
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text, path=tmp_path / "test.pyx")

        index.update(tmp_path / "test.pyx", doc)
        retrieved = index.get(tmp_path / "test.pyx")
        assert retrieved is doc

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        index = WorkspaceIndex(tmp_path)
        assert index.get(tmp_path / "nonexistent.pyx") is None

    def test_update_replaces_existing(
        self, tmp_path: Path, sample_pyx_text: str, python_only_text: str
    ) -> None:
        index = WorkspaceIndex(tmp_path)
        parser = TolerantParser()
        path = tmp_path / "test.pyx"

        doc1 = parser.parse_text(sample_pyx_text, path=path)
        doc2 = parser.parse_text(python_only_text, path=path)

        index.update(path, doc1)
        index.update(path, doc2)
        assert index.get(path) is doc2


class TestRemove:
    """WorkspaceIndex.remove removes a document from the index."""

    def test_remove_existing(self, tmp_path: Path, sample_pyx_text: str) -> None:
        index = WorkspaceIndex(tmp_path)
        parser = TolerantParser()
        path = tmp_path / "test.pyx"
        doc = parser.parse_text(sample_pyx_text, path=path)

        index.update(path, doc)
        index.remove(path)
        assert index.get(path) is None

    def test_remove_nonexistent_is_noop(self, tmp_path: Path) -> None:
        index = WorkspaceIndex(tmp_path)
        # Should not raise.
        index.remove(tmp_path / "ghost.pyx")


class TestFindSymbols:
    """WorkspaceIndex.find_symbols searches across all indexed documents."""

    def test_find_loader_by_name(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        results = index.find_symbols("loader")
        assert len(results) >= 1
        assert all(isinstance(s, WorkspaceSymbol) for s in results)
        names = {s.name for s in results}
        assert "loader" in names

    def test_find_is_case_insensitive(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        lower_results = index.find_symbols("loader")
        upper_results = index.find_symbols("LOADER")
        assert len(lower_results) == len(upper_results)

    def test_find_no_match(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        results = index.find_symbols("zzz_nonexistent_zzz")
        assert len(results) == 0

    def test_results_sorted_by_path_and_line(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        results = index.find_symbols("")  # Match everything.
        for i in range(1, len(results)):
            prev = results[i - 1]
            curr = results[i]
            assert (prev.path, prev.line) <= (curr.path, curr.line)


class TestAllDocuments:
    """WorkspaceIndex.all_documents returns all cached documents."""

    def test_empty_index(self, tmp_path: Path) -> None:
        index = WorkspaceIndex(tmp_path)
        assert len(index.all_documents()) == 0

    def test_after_scan(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        docs = index.all_documents()
        assert len(docs) == 2
        for doc in docs.values():
            assert isinstance(doc, PyxDocument)


class TestScanEmptyDir:
    """Scanning an empty directory (no pages/) works without error."""

    def test_empty_root(self, tmp_path: Path) -> None:
        index = WorkspaceIndex(tmp_path)
        index.scan()
        assert len(index.all_documents()) == 0

    def test_empty_pages_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pages").mkdir()
        index = WorkspaceIndex(tmp_path)
        index.scan()
        assert len(index.all_documents()) == 0

    def test_root_property(self, tmp_path: Path) -> None:
        index = WorkspaceIndex(tmp_path)
        assert index.root == tmp_path


class TestExtractSymbolsFromWorkspace:
    """_extract_symbols covers various AST node types."""

    def test_variable_assignment_found(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        pyx = pages / "test.pyx"
        pyx.write_text(
            dedent("""\
                MY_VAR = 42
                typed_var: int = 10

                def helper():
                    pass
            """),
            encoding="utf-8",
        )
        index = WorkspaceIndex(tmp_path)
        index.scan()
        symbols = index.find_symbols("MY_VAR")
        assert len(symbols) >= 1
        assert symbols[0].kind == "variable"

    def test_annotated_assignment_found(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        pyx = pages / "test.pyx"
        pyx.write_text("typed_var: int = 10\n", encoding="utf-8")
        index = WorkspaceIndex(tmp_path)
        index.scan()
        symbols = index.find_symbols("typed_var")
        assert len(symbols) >= 1
        assert symbols[0].kind == "variable"

    def test_async_function_found(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        pyx = pages / "test.pyx"
        pyx.write_text(
            dedent("""\
                async def my_async():
                    pass
            """),
            encoding="utf-8",
        )
        index = WorkspaceIndex(tmp_path)
        index.scan()
        symbols = index.find_symbols("my_async")
        assert len(symbols) >= 1
        assert symbols[0].kind == "async function"

    def test_class_found(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        pyx = pages / "test.pyx"
        pyx.write_text("class MyClass:\n    pass\n", encoding="utf-8")
        index = WorkspaceIndex(tmp_path)
        index.scan()
        symbols = index.find_symbols("MyClass")
        assert len(symbols) >= 1
        assert symbols[0].kind == "class"

    def test_syntax_error_returns_no_symbols(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        pyx = pages / "test.pyx"
        pyx.write_text("def broken(\n    pass\n", encoding="utf-8")
        index = WorkspaceIndex(tmp_path)
        index.scan()
        symbols = index.find_symbols("")
        # Broken file yields no symbols from the AST extraction.
        file_symbols = [s for s in symbols if s.path == pyx]
        assert len(file_symbols) == 0

    def test_loader_tagged_as_loader(self, tmp_project: Path) -> None:
        index = WorkspaceIndex(tmp_project)
        index.scan()
        symbols = index.find_symbols("loader")
        loader_syms = [s for s in symbols if s.kind == "loader"]
        assert len(loader_syms) >= 1
        assert loader_syms[0].detail == "@server loader"

    def test_action_tagged_as_action(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        pyx = pages / "test.pyx"
        pyx.write_text(
            dedent("""\
                @action
                async def submit(request):
                    pass
                ---
                export default function Page() {
                    return <h1>Hello</h1>;
                }
            """),
            encoding="utf-8",
        )
        index = WorkspaceIndex(tmp_path)
        index.scan()
        symbols = index.find_symbols("submit")
        action_syms = [s for s in symbols if s.kind == "action"]
        assert len(action_syms) >= 1
        assert action_syms[0].detail == "@action"
