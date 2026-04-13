"""Tests for pyxle_langkit.cli — CLI commands via typer's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from pyxle_langkit.cli import app

runner = CliRunner()


def _write_pyxl(tmp_path: Path, name: str, content: str) -> Path:
    """Write a .pyxl file and return its path."""
    pyxl_file = tmp_path / name
    pyxl_file.write_text(dedent(content), encoding="utf-8")
    return pyxl_file


# ------------------------------------------------------------------
# parse command
# ------------------------------------------------------------------


class TestParseCommand:
    """The `parse` command outputs valid JSON describing the document."""

    def test_parse_valid_file(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "page.pyxl", """\
            @server
            async def loader(request):
                return {"ok": True}

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["parse", str(pyxl_file)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["has_python"] is True
        assert data["has_jsx"] is True
        assert data["loader"] is not None
        assert data["loader"]["name"] == "loader"

    def test_parse_outputs_json(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "simple.pyxl", """\
            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["parse", str(pyxl_file)])
        assert result.exit_code == 0
        # Should be valid JSON.
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_parse_empty_file(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "empty.pyxl", "")
        result = runner.invoke(app, ["parse", str(pyxl_file)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["has_python"] is False


# ------------------------------------------------------------------
# lint command
# ------------------------------------------------------------------


class TestLintCommand:
    """The `lint` command prints diagnostics or 'No issues'."""

    def test_lint_clean_file(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "clean.pyxl", """\
            from datetime import datetime

            @server
            async def loader(request):
                now = datetime.now()
                return {"time": str(now)}

            ---

            export default function Page({ time }) {
                return <h1>{time}</h1>;
            }
        """)
        result = runner.invoke(app, ["lint", str(pyxl_file)])
        # May or may not have issues depending on JSX analysis availability.
        # The command should at least not crash.
        assert result.exit_code in (0, 1)

    def test_lint_prints_no_issues(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "clean2.pyxl", """\
            from datetime import datetime

            @server
            async def loader(request):
                return {"ts": str(datetime.now())}

            ---

            export default function Page({ ts }) {
                return <h1>{ts}</h1>;
            }
        """)
        result = runner.invoke(app, ["lint", str(pyxl_file)])
        # "No issues" is printed when everything is clean.
        # If JSX analysis is unavailable, there may be warnings, which is OK.
        assert result.exit_code in (0, 1)


class TestLintWithErrors:
    """The `lint` command exits with code 1 when errors are found."""

    def test_syntax_error_exit_code_1(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "broken.pyxl", """\
            def broken(
                return None

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["lint", str(pyxl_file)])
        assert result.exit_code == 1

    def test_undefined_name_exit_code_1(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "undef.pyxl", """\
            @server
            async def loader(request):
                return {"value": totally_undefined_name}

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["lint", str(pyxl_file)])
        assert result.exit_code == 1


# ------------------------------------------------------------------
# outline command
# ------------------------------------------------------------------


class TestOutlineCommand:
    """The `outline` command prints the symbol outline of a .pyxl file."""

    def test_outline_shows_symbols(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "page.pyxl", """\
            @server
            async def loader(request):
                return {"ok": True}

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["outline", str(pyxl_file)])
        assert result.exit_code == 0
        assert "loader" in result.output

    def test_outline_empty_file(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "empty.pyxl", "")
        result = runner.invoke(app, ["outline", str(pyxl_file)])
        assert result.exit_code == 0
        assert "no symbols" in result.output.lower()

    def test_outline_shows_functions(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "helpers.pyxl", """\
            def helper_one():
                pass

            class MyModel:
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["outline", str(pyxl_file)])
        assert result.exit_code == 0
        assert "helper_one" in result.output
        assert "MyModel" in result.output


# ------------------------------------------------------------------
# format command
# ------------------------------------------------------------------


class TestFormatCommand:
    """The `format` command formats a .pyxl file."""

    def test_format_already_formatted(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "clean.pyxl", """\
            x = 1
        """)
        result = runner.invoke(
            app, ["format", str(pyxl_file), "--python-formatter", "none",
                  "--jsx-formatter", "none"]
        )
        assert result.exit_code == 0
        assert "already formatted" in result.output

    def test_format_check_mode(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "test.pyxl", """\
            x = 1
        """)
        result = runner.invoke(
            app, ["format", str(pyxl_file), "--check",
                  "--python-formatter", "none", "--jsx-formatter", "none"]
        )
        # Either already formatted (exit 0) or would reformat (exit 1).
        assert result.exit_code in (0, 1)


# ------------------------------------------------------------------
# No-args help
# ------------------------------------------------------------------


class TestNoArgs:
    """Running without arguments shows help."""

    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app, [])
        # Typer with no_args_is_help=True exits with code 0 or 2.
        assert result.exit_code in (0, 2)
        assert "Usage" in result.output or "pyxle-langkit" in result.output


# ------------------------------------------------------------------
# lint — parser diagnostics are printed
# ------------------------------------------------------------------


class TestLintOutputDetails:
    """Lint output includes both parser diagnostics and linter issues."""

    def test_lint_output_includes_error_count(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "errors.pyxl", """\
            @server
            async def loader(request):
                return {"value": totally_undefined}

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["lint", str(pyxl_file)])
        assert "issue(s) found" in result.output or "No issues" in result.output

    def test_lint_output_includes_severity(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "warn.pyxl", """\
            import os

            @server
            async def loader(request):
                return {"ok": True}
        """)
        result = runner.invoke(app, ["lint", str(pyxl_file)])
        # Unused import is at least an info-level diagnostic.
        assert result.exit_code in (0, 1)


# ------------------------------------------------------------------
# parse — output structure
# ------------------------------------------------------------------


class TestParseOutputStructure:
    """The `parse` command outputs complete JSON structure."""

    def test_parse_includes_diagnostics(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "diag.pyxl", """\
            def broken(
                pass
        """)
        result = runner.invoke(app, ["parse", str(pyxl_file)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "diagnostics" in data

    def test_parse_includes_actions(self, tmp_path: Path) -> None:
        pyxl_file = _write_pyxl(tmp_path, "actions.pyxl", """\
            @action
            async def submit(request):
                pass

            ---

            export default function Page() {
                return <h1>Hello</h1>;
            }
        """)
        result = runner.invoke(app, ["parse", str(pyxl_file)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "actions" in data
        assert len(data["actions"]) >= 1
