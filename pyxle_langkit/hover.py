"""Hover information provider for ``.pyxl`` files.

Combines Jedi-based Python hover with Pyxle-specific documentation
for decorators, components, and data properties.
"""

from __future__ import annotations

import ast
import logging
import re

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
# Pyxle decorator docs
# ------------------------------------------------------------------

_SERVER_HOVER = """\
**@server** — Pyxle Loader

Marks an async function as the page's data loader. The function receives \
a Starlette `Request` object and must return a JSON-serializable dict.

The returned dict is available as `data` in the JSX section.

```python
@server
async def loader(request: Request):
    return {"user": get_user(request)}
```

- Runs on every page request (SSR and client navigation).
- Must be `async`.
- Only one `@server` function per `.pyxl` file.
"""

_ACTION_HOVER = """\
**@action** — Pyxle Server Action

Marks an async function as a server action callable from React \
via the `useAction` hook.

```python
@action
async def submit_form(request: Request):
    data = await request.json()
    return {"success": True}
```

- Receives a Starlette `Request` object.
- Must return a JSON-serializable dict.
- Raise `ActionError` for structured error responses.
- Multiple `@action` functions per `.pyxl` file are allowed.
"""

# ------------------------------------------------------------------
# Pyxle component docs
# ------------------------------------------------------------------

_COMPONENT_DOCS: dict[str, str] = {
    "Link": """\
**Link** — Client-Side Navigation

```jsx
<Link href="/about" prefetch={true}>About</Link>
```

| Prop | Type | Description |
|------|------|-------------|
| `href` | `string` | Target URL (required). |
| `prefetch` | `boolean` | Prefetch on hover (default: `true`). |
| `replace` | `boolean` | Replace history entry instead of pushing. |
| `scroll` | `boolean` | Scroll to top after navigation (default: `true`). |
""",
    "Script": """\
**Script** — Managed Script Tag

```jsx
<Script src="/analytics.js" strategy="afterInteractive" />
```

| Prop | Type | Description |
|------|------|-------------|
| `src` | `string` | Script URL (required). |
| `strategy` | `string` | `"beforeInteractive"`, `"afterInteractive"`, `"lazyOnload"`. |
| `async` | `boolean` | HTML async attribute. |
| `defer` | `boolean` | HTML defer attribute. |
| `module` | `boolean` | Load as ES module. |
| `noModule` | `boolean` | Load as nomodule fallback. |
""",
    "Image": """\
**Image** — Optimised Image

```jsx
<Image src="/hero.jpg" width={800} height={600} alt="Hero" />
```

| Prop | Type | Description |
|------|------|-------------|
| `src` | `string` | Image source URL (required). |
| `width` | `number` | Intrinsic width in pixels. |
| `height` | `number` | Intrinsic height in pixels. |
| `alt` | `string` | Alt text for accessibility. |
| `priority` | `boolean` | Eager load (above the fold). |
| `lazy` | `boolean` | Lazy load (default: `true`). |
""",
    "Head": """\
**Head** — Document Head Container

```jsx
<Head>
  <title>My Page</title>
  <meta name="description" content="..." />
</Head>
```

Place `<title>`, `<meta>`, `<link>`, and other head elements inside. \
Elements are deduplicated and merged with layout heads.
""",
    "Slot": """\
**Slot** — Layout Slot

```jsx
<Slot name="sidebar" />
```

| Prop | Type | Description |
|------|------|-------------|
| `name` | `string` | Slot name for layout composition. |
""",
    "ClientOnly": """\
**ClientOnly** — Client-Only Rendering

```jsx
<ClientOnly>
  <BrowserOnlyWidget />
</ClientOnly>
```

Children are rendered only in the browser. During SSR, the component \
renders nothing. Useful for browser-API-dependent code.
""",
    "Form": """\
**Form** — Enhanced Form

```jsx
<Form action={submitForm} method="post">
  <input name="email" />
  <button type="submit">Submit</button>
</Form>
```

| Prop | Type | Description |
|------|------|-------------|
| `action` | `function` | Server action to call on submit. |
| `method` | `string` | HTTP method (default: `"post"`). |
""",
}

# ------------------------------------------------------------------
# Context detection patterns
# ------------------------------------------------------------------

_DECORATOR_RE = re.compile(r"^\s*@(server|action)\s*$")
_COMPONENT_TAG_RE = re.compile(r"<\s*([A-Z]\w+)")
_DATA_PROP_RE = re.compile(r"\bdata\b")


# ------------------------------------------------------------------
# Hover provider
# ------------------------------------------------------------------


class HoverProvider:
    """Provides hover information for ``.pyxl`` files."""

    def hover(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> str | None:
        """Provide hover information at the given position.

        *line* is 1-indexed, *column* is 0-indexed.
        Returns a Markdown string or ``None`` if no hover info is available.
        """
        section = document.section_at_line(line)

        if section == "python":
            return self._hover_python(document, line, column)
        if section == "jsx":
            return self._hover_jsx(document, line, column)

        return None

    # ------------------------------------------------------------------
    # Python hover
    # ------------------------------------------------------------------

    def _hover_python(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> str | None:
        """Provide hover for Python sections."""
        # Check for Pyxle decorator hover first.
        line_text = _get_python_line_text(document, line)
        if line_text is not None:
            decorator_match = _DECORATOR_RE.match(line_text)
            if decorator_match is not None:
                name = decorator_match.group(1)
                if name == "server":
                    return _SERVER_HOVER
                if name == "action":
                    return _ACTION_HOVER

        # Fall back to Jedi hover.
        return self._hover_jedi(document, line, column)

    def _hover_jedi(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> str | None:
        """Provide hover via Jedi's help API."""
        if jedi is None:
            return None

        virtual_code, line_numbers = document.virtual_python_for_jedi()
        if not virtual_code.strip():
            return None

        virtual_line = _map_to_virtual_line(line, line_numbers)
        if virtual_line is None:
            return None

        path = str(document.path) if document.path else None
        try:
            script = jedi.Script(virtual_code, path=path)
            names = script.help(virtual_line, column)
        except Exception:
            logger.debug("Jedi help failed", exc_info=True)
            return None

        if not names:
            return None

        name_obj = names[0]
        parts: list[str] = []

        # Type signature.
        sigs = getattr(name_obj, "get_signatures", lambda: [])()
        if sigs:
            sig_str = sigs[0].to_string()
            parts.append(f"```python\n{sig_str}\n```")

        # Module path.
        module_name = getattr(name_obj, "module_name", "")
        if module_name:
            parts.append(f"*{module_name}*")

        # Docstring.
        docstring = getattr(name_obj, "docstring", lambda: "")()
        if docstring:
            parts.append(docstring)

        return "\n\n".join(parts) if parts else None

    # ------------------------------------------------------------------
    # JSX hover
    # ------------------------------------------------------------------

    def _hover_jsx(
        self,
        document: PyxDocument,
        line: int,
        column: int,
    ) -> str | None:
        """Provide hover for JSX sections."""
        line_text = _get_jsx_line_text(document, line)
        if line_text is None:
            return None

        # Check for component tag hover.
        tag_match = _COMPONENT_TAG_RE.search(line_text)
        if tag_match is not None:
            component_name = tag_match.group(1)
            tag_start = tag_match.start(1)
            tag_end = tag_match.end(1)
            if tag_start <= column < tag_end:
                doc = _COMPONENT_DOCS.get(component_name)
                if doc is not None:
                    return doc

        # Check for data prop hover.
        if _DATA_PROP_RE.search(line_text):
            # Find if cursor is on 'data'.
            data_start = line_text.find("data")
            if data_start != -1 and data_start <= column < data_start + 4:
                return self._data_hover(document)

        return None

    def _data_hover(self, document: PyxDocument) -> str | None:
        """Provide hover for the ``data`` object in JSX."""
        if document.loader is None:
            return "**data** — No `@server` loader defined in this file."

        keys = _infer_loader_return_keys(document)
        if not keys:
            return (
                f"**data** — Loaded by `{document.loader.name}()`\n\n"
                "Return type could not be inferred."
            )

        lines = [f"**data** — Loaded by `{document.loader.name}()`\n"]
        lines.append("```typescript")
        lines.append("{")
        for key in keys:
            lines.append(f"  {key}: any;")
        lines.append("}")
        lines.append("```")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _map_to_virtual_line(
    pyx_line: int,
    virtual_line_numbers: tuple[int, ...],
) -> int | None:
    """Map a 1-indexed .pyxl line to a 1-indexed virtual Python line."""
    for virtual_idx, orig_line in enumerate(virtual_line_numbers):
        if orig_line == pyx_line:
            return virtual_idx + 1
    return None


def _get_python_line_text(document: PyxDocument, pyx_line: int) -> str | None:
    """Get the text of a Python line corresponding to a .pyxl line number."""
    py_lines = document.python_code.splitlines()
    for py_idx, orig_line in enumerate(document.python_line_numbers):
        if orig_line == pyx_line and py_idx < len(py_lines):
            return py_lines[py_idx]
    return None


def _get_jsx_line_text(document: PyxDocument, pyx_line: int) -> str | None:
    """Get the text of a JSX line corresponding to a .pyxl line number."""
    jsx_lines = document.jsx_code.splitlines()
    for jsx_idx, orig_line in enumerate(document.jsx_line_numbers):
        if orig_line == pyx_line and jsx_idx < len(jsx_lines):
            return jsx_lines[jsx_idx]
    return None


def _infer_loader_return_keys(document: PyxDocument) -> tuple[str, ...]:
    """Infer the dict keys returned by the ``@server`` loader.

    Reuses the same AST-parsing logic as the completion provider.
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


def _extract_return_dict_keys(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    """Extract string keys from dict return statements."""
    keys: list[str] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and node.value is not None:
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.append(key.value)
    return tuple(dict.fromkeys(keys))
