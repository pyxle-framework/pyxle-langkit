"""Tests for pyxle_langkit.diagnostics — diagnostic conversion utilities."""

from __future__ import annotations

from lsprotocol.types import DiagnosticSeverity

from pyxle.compiler.exceptions import CompilationError
from pyxle.compiler.parser import PyxDiagnostic
from pyxle_langkit.diagnostics import (
    compilation_error_to_lsp_diagnostic,
    lint_issues_to_lsp_diagnostics,
    parser_diagnostics_to_lsp,
)
from pyxle_langkit.linter import LintIssue


# ------------------------------------------------------------------
# lint_issues_to_lsp_diagnostics
# ------------------------------------------------------------------


class TestLintIssuesToLsp:
    """Convert LintIssue instances to LSP Diagnostic objects."""

    def test_basic_conversion(self) -> None:
        issue = LintIssue(
            source="python",
            message="'os' imported but unused",
            rule="pyflakes/UnusedImport",
            severity="info",
            line=5,
            column=1,
        )
        diagnostics = lint_issues_to_lsp_diagnostics([issue])
        assert len(diagnostics) == 1

        diag = diagnostics[0]
        assert diag.message == "'os' imported but unused"
        assert diag.severity == DiagnosticSeverity.Information
        # Line is 0-indexed in LSP: 5 -> 4.
        assert diag.range.start.line == 4

    def test_multiple_issues(self) -> None:
        issues = [
            LintIssue(
                source="python",
                message="error one",
                rule="rule/A",
                severity="error",
                line=1,
                column=0,
            ),
            LintIssue(
                source="python",
                message="warning two",
                rule="rule/B",
                severity="warning",
                line=10,
                column=5,
            ),
        ]
        diagnostics = lint_issues_to_lsp_diagnostics(issues)
        assert len(diagnostics) == 2
        assert diagnostics[0].severity == DiagnosticSeverity.Error
        assert diagnostics[1].severity == DiagnosticSeverity.Warning

    def test_empty_list(self) -> None:
        assert lint_issues_to_lsp_diagnostics([]) == []

    def test_source_preserved(self) -> None:
        issue = LintIssue(
            source="python",
            message="test",
            rule="test/rule",
            severity="error",
            line=1,
            column=0,
        )
        diagnostics = lint_issues_to_lsp_diagnostics([issue])
        assert diagnostics[0].source == "python"


# ------------------------------------------------------------------
# compilation_error_to_lsp_diagnostic
# ------------------------------------------------------------------


class TestCompilationErrorToDiagnostic:
    """Convert CompilationError to an LSP Diagnostic."""

    def test_with_line_number(self) -> None:
        exc = CompilationError(message="Unexpected token", line_number=10)
        diag = compilation_error_to_lsp_diagnostic(exc)
        assert diag.message == "Unexpected token"
        assert diag.severity == DiagnosticSeverity.Error
        # Line 10 -> 0-indexed line 9.
        assert diag.range.start.line == 9
        assert diag.source == "pyxle-parser"

    def test_without_line_number(self) -> None:
        exc = CompilationError(message="Parse failed", line_number=None)
        diag = compilation_error_to_lsp_diagnostic(exc)
        assert diag.range.start.line == 0
        assert diag.message == "Parse failed"

    def test_negative_line_defaults_to_zero(self) -> None:
        exc = CompilationError(message="Bad line", line_number=-3)
        diag = compilation_error_to_lsp_diagnostic(exc)
        assert diag.range.start.line == 0


# ------------------------------------------------------------------
# parser_diagnostics_to_lsp
# ------------------------------------------------------------------


class TestParserDiagnosticsToLsp:
    """Convert PyxDiagnostic entries to LSP Diagnostic objects."""

    def test_python_section_source(self) -> None:
        pyx_diag = PyxDiagnostic(
            section="python",
            severity="error",
            message="SyntaxError: invalid syntax",
            line=5,
        )
        diagnostics = parser_diagnostics_to_lsp([pyx_diag])
        assert len(diagnostics) == 1
        assert diagnostics[0].source == "pyxle-parser"

    def test_jsx_section_source(self) -> None:
        pyx_diag = PyxDiagnostic(
            section="jsx",
            severity="error",
            message="Unexpected token <",
            line=12,
        )
        diagnostics = parser_diagnostics_to_lsp([pyx_diag])
        assert len(diagnostics) == 1
        assert diagnostics[0].source == "babel"

    def test_line_conversion(self) -> None:
        pyx_diag = PyxDiagnostic(
            section="python",
            severity="warning",
            message="possible issue",
            line=7,
        )
        diagnostics = parser_diagnostics_to_lsp([pyx_diag])
        # Line 7 -> 0-indexed line 6.
        assert diagnostics[0].range.start.line == 6

    def test_empty_list(self) -> None:
        assert parser_diagnostics_to_lsp([]) == []

    def test_none_line_defaults_to_zero(self) -> None:
        pyx_diag = PyxDiagnostic(
            section="python",
            severity="error",
            message="unknown position",
            line=None,
        )
        diagnostics = parser_diagnostics_to_lsp([pyx_diag])
        assert diagnostics[0].range.start.line == 0


# ------------------------------------------------------------------
# Severity mapping
# ------------------------------------------------------------------


class TestSeverityMapping:
    """Severity strings map to the correct LSP DiagnosticSeverity."""

    def test_error_maps_to_error(self) -> None:
        issue = LintIssue(
            source="python", message="x", rule="x", severity="error",
            line=1, column=0,
        )
        diag = lint_issues_to_lsp_diagnostics([issue])[0]
        assert diag.severity == DiagnosticSeverity.Error

    def test_warning_maps_to_warning(self) -> None:
        issue = LintIssue(
            source="python", message="x", rule="x", severity="warning",
            line=1, column=0,
        )
        diag = lint_issues_to_lsp_diagnostics([issue])[0]
        assert diag.severity == DiagnosticSeverity.Warning

    def test_info_maps_to_information(self) -> None:
        issue = LintIssue(
            source="python", message="x", rule="x", severity="info",
            line=1, column=0,
        )
        diag = lint_issues_to_lsp_diagnostics([issue])[0]
        assert diag.severity == DiagnosticSeverity.Information


class TestNullLineDefaultsToZero:
    """When line is None or zero, it defaults to LSP line 0."""

    def test_pyx_diagnostic_none_line(self) -> None:
        pyx_diag = PyxDiagnostic(
            section="python",
            severity="error",
            message="no line",
            line=None,
        )
        diagnostics = parser_diagnostics_to_lsp([pyx_diag])
        assert diagnostics[0].range.start.line == 0

    def test_compilation_error_none_line(self) -> None:
        exc = CompilationError(message="no line", line_number=None)
        diag = compilation_error_to_lsp_diagnostic(exc)
        assert diag.range.start.line == 0

    def test_lint_issue_zero_line(self) -> None:
        issue = LintIssue(
            source="python", message="x", rule="x", severity="error",
            line=0, column=0,
        )
        diag = lint_issues_to_lsp_diagnostics([issue])[0]
        assert diag.range.start.line == 0
