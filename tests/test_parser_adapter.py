"""Tests for pyxle_langkit.parser_adapter — TolerantParser."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pyxle.compiler.exceptions import CompilationError
from pyxle_langkit.document import PyxDocument
from pyxle_langkit.parser_adapter import TolerantParser


class TestParseTextValid:
    """TolerantParser.parse_text on valid input returns a usable PyxDocument."""

    def test_returns_pyx_document(self, sample_pyx_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text)
        assert isinstance(doc, PyxDocument)

    def test_has_python_and_jsx(self, sample_pyx_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text)
        assert doc.has_python
        assert doc.has_jsx

    def test_python_code_contains_loader(self, sample_pyx_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text)
        assert "async def loader" in doc.python_code

    def test_jsx_code_contains_export(self, sample_pyx_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text)
        assert "export default" in doc.jsx_code

    def test_no_diagnostics_on_valid_python_only(self, python_only_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(python_only_text)
        assert doc.diagnostics == ()

    def test_separator_produces_expected_diagnostic(
        self, sample_pyx_text: str
    ) -> None:
        # The --- separator line is not valid Python, so the tolerant parser
        # records it as a diagnostic. This is expected behavior.
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text)
        separator_diags = [
            d for d in doc.diagnostics if "invalid syntax" in d.message
        ]
        assert len(separator_diags) >= 1

    def test_loader_metadata_extracted(self, sample_pyx_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(sample_pyx_text)
        assert doc.loader is not None
        assert doc.loader.name == "loader"
        assert doc.loader.is_async is True


class TestParseTextSyntaxError:
    """Broken Python produces a diagnostic instead of raising."""

    def test_syntax_error_produces_diagnostic(self, syntax_error_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(syntax_error_text)
        assert isinstance(doc, PyxDocument)
        assert len(doc.diagnostics) > 0
        errors = [d for d in doc.diagnostics if d.severity == "error"]
        assert len(errors) > 0

    def test_syntax_error_does_not_raise(self, syntax_error_text: str) -> None:
        parser = TolerantParser()
        # Must not raise any exception.
        doc = parser.parse_text(syntax_error_text)
        assert doc is not None


class TestParseTextEmpty:
    """Empty string produces an empty document without crashing."""

    def test_empty_returns_document(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text("")
        assert isinstance(doc, PyxDocument)

    def test_empty_has_no_python(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text("")
        assert not doc.has_python

    def test_empty_has_no_jsx(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text("")
        assert not doc.has_jsx

    def test_empty_has_no_diagnostics(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text("")
        assert doc.diagnostics == ()


class TestParseFile:
    """TolerantParser.parse reads from disk."""

    def test_parse_file_works(self, tmp_path: Path, sample_pyx_text: str) -> None:
        pyx_file = tmp_path / "page.pyx"
        pyx_file.write_text(sample_pyx_text, encoding="utf-8")

        parser = TolerantParser()
        doc = parser.parse(pyx_file)
        assert isinstance(doc, PyxDocument)
        assert doc.path == pyx_file
        assert doc.has_python

    def test_parse_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.pyx"
        parser = TolerantParser()
        doc = parser.parse(missing)
        assert isinstance(doc, PyxDocument)
        assert len(doc.diagnostics) > 0
        assert "Cannot read file" in doc.diagnostics[0].message

    def test_parse_file_not_found_no_raise(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.pyx"
        parser = TolerantParser()
        doc = parser.parse(missing)
        assert doc is not None


class TestParserNeverRaises:
    """Even with garbage input the parser returns a PyxDocument."""

    @pytest.mark.parametrize("garbage", [
        "}{}{}{",
        "def ((((((",
        "\x00\x01\x02",
        "---\n---\n---\n---",
        "@server\nasync def loader(",
        "export default {\n\n\n",
    ])
    def test_garbage_input_returns_document(self, garbage: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(garbage)
        assert isinstance(doc, PyxDocument)

    def test_none_like_whitespace(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text("   \n\n   \t  ")
        assert isinstance(doc, PyxDocument)


class TestCompilationErrorPath:
    """TolerantParser catches CompilationError despite tolerant=True."""

    def test_compilation_error_becomes_diagnostic(self) -> None:
        parser = TolerantParser()
        exc = CompilationError(message="forced error", line_number=5)
        with patch.object(
            parser._parser, "parse_text", side_effect=exc
        ):
            doc = parser.parse_text("some code")
        assert isinstance(doc, PyxDocument)
        assert len(doc.diagnostics) == 1
        assert doc.diagnostics[0].message == "forced error"
        assert doc.diagnostics[0].line == 5
        assert doc.diagnostics[0].severity == "error"

    def test_compilation_error_preserves_source(self) -> None:
        parser = TolerantParser()
        exc = CompilationError(message="forced", line_number=None)
        with patch.object(
            parser._parser, "parse_text", side_effect=exc
        ):
            doc = parser.parse_text("my source")
        assert doc.source == "my source"


class TestUnexpectedExceptionPath:
    """TolerantParser catches unexpected exceptions (e.g. bugs in parser)."""

    def test_runtime_error_becomes_diagnostic(self) -> None:
        parser = TolerantParser()
        with patch.object(
            parser._parser, "parse_text", side_effect=RuntimeError("boom")
        ):
            doc = parser.parse_text("code")
        assert isinstance(doc, PyxDocument)
        assert len(doc.diagnostics) == 1
        assert "Internal parser error" in doc.diagnostics[0].message
        assert "RuntimeError" in doc.diagnostics[0].message
        assert "boom" in doc.diagnostics[0].message

    def test_type_error_becomes_diagnostic(self) -> None:
        parser = TolerantParser()
        with patch.object(
            parser._parser, "parse_text", side_effect=TypeError("bad type")
        ):
            doc = parser.parse_text("code")
        assert isinstance(doc, PyxDocument)
        assert "TypeError" in doc.diagnostics[0].message


class TestParseTextPath:
    """Additional parse_text path tests for coverage."""

    def test_path_argument_stored(self, tmp_path: Path) -> None:
        parser = TolerantParser()
        p = tmp_path / "test.pyx"
        doc = parser.parse_text("x = 1", path=p)
        assert doc.path == p

    def test_path_none_by_default(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text("x = 1")
        assert doc.path is None
