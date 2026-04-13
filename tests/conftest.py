"""Shared fixtures for pyxle-langkit tests."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from pyxle_langkit.document import PyxDocument
from pyxle_langkit.parser_adapter import TolerantParser


# ------------------------------------------------------------------
# Text fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def sample_pyxl_text() -> str:
    """A basic .pyxl file with both Python and JSX sections."""
    return dedent("""\
        from datetime import datetime

        @server
        async def loader(request):
            now = datetime.now()
            return {"time": str(now)}

        ---

        export default function Page({ time }) {
            return <h1>The time is {time}</h1>;
        }
    """)


@pytest.fixture()
def python_only_text() -> str:
    """A .pyxl file containing only Python code."""
    return dedent("""\
        import os

        @server
        async def loader(request):
            return {"cwd": os.getcwd()}
    """)


@pytest.fixture()
def jsx_only_text() -> str:
    """A .pyxl file containing only JSX code."""
    return dedent("""\
        export default function Page() {
            return <h1>Hello world</h1>;
        }
    """)


@pytest.fixture()
def empty_text() -> str:
    """An empty string."""
    return ""


@pytest.fixture()
def syntax_error_text() -> str:
    """A .pyxl file with a Python syntax error."""
    return dedent("""\
        def broken(
            return None

        ---

        export default function Page() {
            return <h1>Broken</h1>;
        }
    """)


@pytest.fixture()
def parsed_document(sample_pyxl_text: str) -> PyxDocument:
    """A PyxDocument parsed from the sample_pyxl_text fixture."""
    parser = TolerantParser()
    return parser.parse_text(sample_pyxl_text)


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with pages/ and a sample .pyxl file.

    Returns the root project directory.
    """
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()

    index_pyxl = pages_dir / "index.pyxl"
    index_pyxl.write_text(
        dedent("""\
            @server
            async def loader(request):
                return {"title": "Home"}

            ---

            export default function Home({ title }) {
                return <h1>{title}</h1>;
            }
        """),
        encoding="utf-8",
    )

    about_pyxl = pages_dir / "about.pyxl"
    about_pyxl.write_text(
        dedent("""\
            export default function About() {
                return <h1>About</h1>;
            }
        """),
        encoding="utf-8",
    )

    return tmp_path
