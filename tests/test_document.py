"""Tests for pyxle_langkit.document — PyxDocument."""

from __future__ import annotations

from textwrap import dedent

from pyxle_langkit.document import PyxDocument
from pyxle_langkit.parser_adapter import TolerantParser


# ------------------------------------------------------------------
# Line mapping: Python
# ------------------------------------------------------------------


class TestMapPythonLine:
    """PyxDocument.map_python_line forward-maps virtual lines to .pyxl lines."""

    def test_first_python_line(self, parsed_document: PyxDocument) -> None:
        result = parsed_document.map_python_line(1)
        assert result is not None
        assert isinstance(result, int)
        assert result >= 1

    def test_maps_correctly_within_range(self, parsed_document: PyxDocument) -> None:
        # Every valid virtual line should produce a positive integer.
        for vline in range(1, len(parsed_document.python_line_numbers) + 1):
            pyx_line = parsed_document.map_python_line(vline)
            assert pyx_line is not None
            assert pyx_line >= 1

    def test_out_of_range_high(self, parsed_document: PyxDocument) -> None:
        beyond = len(parsed_document.python_line_numbers) + 100
        assert parsed_document.map_python_line(beyond) is None

    def test_out_of_range_zero(self, parsed_document: PyxDocument) -> None:
        assert parsed_document.map_python_line(0) is None

    def test_out_of_range_negative(self, parsed_document: PyxDocument) -> None:
        assert parsed_document.map_python_line(-1) is None


# ------------------------------------------------------------------
# Line mapping: JSX
# ------------------------------------------------------------------


class TestMapJsxLine:
    """PyxDocument.map_jsx_line forward-maps virtual JSX lines to .pyxl lines."""

    def test_first_jsx_line(self, parsed_document: PyxDocument) -> None:
        result = parsed_document.map_jsx_line(1)
        assert result is not None
        assert isinstance(result, int)
        assert result >= 1

    def test_maps_correctly_within_range(self, parsed_document: PyxDocument) -> None:
        for vline in range(1, len(parsed_document.jsx_line_numbers) + 1):
            pyx_line = parsed_document.map_jsx_line(vline)
            assert pyx_line is not None
            assert pyx_line >= 1

    def test_out_of_range_high(self, parsed_document: PyxDocument) -> None:
        beyond = len(parsed_document.jsx_line_numbers) + 100
        assert parsed_document.map_jsx_line(beyond) is None

    def test_out_of_range_zero(self, parsed_document: PyxDocument) -> None:
        assert parsed_document.map_jsx_line(0) is None

    def test_out_of_range_negative(self, parsed_document: PyxDocument) -> None:
        assert parsed_document.map_jsx_line(-1) is None


# ------------------------------------------------------------------
# Section detection
# ------------------------------------------------------------------


class TestSectionAtLine:
    """PyxDocument.section_at_line identifies python/jsx/unknown."""

    def test_python_lines_identified(self, parsed_document: PyxDocument) -> None:
        for pyx_line in parsed_document.python_line_numbers:
            assert parsed_document.section_at_line(pyx_line) == "python"

    def test_jsx_lines_identified(self, parsed_document: PyxDocument) -> None:
        for pyx_line in parsed_document.jsx_line_numbers:
            assert parsed_document.section_at_line(pyx_line) == "jsx"

    def test_out_of_range_is_unknown(self, parsed_document: PyxDocument) -> None:
        assert parsed_document.section_at_line(9999) == "unknown"

    def test_separator_line_is_unknown(self, parsed_document: PyxDocument) -> None:
        # The --- separator line should not be in either map.
        # Find a line not in python or jsx.
        all_mapped = set(parsed_document.python_line_numbers) | set(
            parsed_document.jsx_line_numbers
        )
        # Line numbers start at 1; check a few around the separator.
        max_line = max(all_mapped) if all_mapped else 0
        for line in range(1, max_line + 1):
            if line not in all_mapped:
                assert parsed_document.section_at_line(line) == "unknown"
                break


# ------------------------------------------------------------------
# Jedi virtual Python
# ------------------------------------------------------------------


class TestVirtualPythonForJedi:
    """PyxDocument.virtual_python_for_jedi injects imports and annotations."""

    def test_injects_request_import(self, parsed_document: PyxDocument) -> None:
        code, _ = parsed_document.virtual_python_for_jedi()
        assert "from starlette.requests import Request" in code

    def test_injects_server_import_when_loader_present(
        self, parsed_document: PyxDocument
    ) -> None:
        code, _ = parsed_document.virtual_python_for_jedi()
        assert "server" in code

    def test_annotates_request_parameter(self, parsed_document: PyxDocument) -> None:
        code, _ = parsed_document.virtual_python_for_jedi()
        assert "request: Request" in code

    def test_line_numbers_match_code_lines(
        self, parsed_document: PyxDocument
    ) -> None:
        code, line_numbers = parsed_document.virtual_python_for_jedi()
        lines = code.split("\n")
        # Strip trailing empty line if present.
        if lines and lines[-1] == "":
            lines = lines[:-1]
        assert len(line_numbers) == len(lines)

    def test_injected_lines_have_zero_line_number(
        self, parsed_document: PyxDocument
    ) -> None:
        _, line_numbers = parsed_document.virtual_python_for_jedi()
        # At least one injected line (the Request import) should map to 0.
        assert 0 in line_numbers

    def test_empty_python_returns_empty(self) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(dedent("""\
            export default function Page() {
                return <h1>Hello</h1>;
            }
        """))
        code, line_numbers = doc.virtual_python_for_jedi()
        assert code.strip() == ""

    def test_action_import_injected(self) -> None:
        text = dedent("""\
            @action
            async def submit(request):
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        parser = TolerantParser()
        doc = parser.parse_text(text)
        code, _ = doc.virtual_python_for_jedi()
        assert "action" in code

    def test_both_loader_and_action(self) -> None:
        text = dedent("""\
            @server
            async def loader(request):
                return {"ok": True}

            @action
            async def submit(request):
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        parser = TolerantParser()
        doc = parser.parse_text(text)
        code, line_numbers = doc.virtual_python_for_jedi()
        assert "from starlette.requests import Request" in code
        assert "server" in code
        assert "action" in code
        assert "request: Request" in code

    def test_loader_only_injects_server(self) -> None:
        text = dedent("""\
            @server
            async def loader(request):
                return {"ok": True}
        """)
        parser = TolerantParser()
        doc = parser.parse_text(text)
        code, _ = doc.virtual_python_for_jedi()
        assert "server" in code


# ------------------------------------------------------------------
# Convenience properties
# ------------------------------------------------------------------


class TestHasPythonAndHasJsx:
    """PyxDocument.has_python and has_jsx properties."""

    def test_both_sections(self, parsed_document: PyxDocument) -> None:
        assert parsed_document.has_python is True
        assert parsed_document.has_jsx is True

    def test_python_only(self, python_only_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(python_only_text)
        assert doc.has_python is True
        assert doc.has_jsx is False

    def test_jsx_only(self, jsx_only_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(jsx_only_text)
        # JSX-only files have no separator, so the parser may treat
        # everything as one section. Check at least one is truthy.
        assert doc.has_python or doc.has_jsx


class TestEmptyDocument:
    """Empty document does not crash on any method."""

    def test_no_crash_on_map_python_line(self, empty_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(empty_text)
        assert doc.map_python_line(1) is None

    def test_no_crash_on_map_jsx_line(self, empty_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(empty_text)
        assert doc.map_jsx_line(1) is None

    def test_no_crash_on_section_at_line(self, empty_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(empty_text)
        assert doc.section_at_line(1) == "unknown"

    def test_no_crash_on_virtual_python(self, empty_text: str) -> None:
        parser = TolerantParser()
        doc = parser.parse_text(empty_text)
        code, line_numbers = doc.virtual_python_for_jedi()
        assert isinstance(code, str)
        assert isinstance(line_numbers, tuple)
