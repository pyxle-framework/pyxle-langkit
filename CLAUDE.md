# CLAUDE.md — Pyxle Langkit v2

Language tooling suite for Pyxle — powers IDE support for `.pyxl` files.

---

## Project Overview

Pyxle Langkit provides IDE support for `.pyxl` files — the hybrid Python/JSX format
used by the Pyxle framework. The architecture is **server-driven**: all intelligence
lives in the Python LSP server, and the VS Code extension is a thin LSP client.

**Key architecture decisions:**
- **jedi** for Python completions, definitions, hover, references
- **pyflakes** for Python static analysis
- **Babel** (Node.js subprocess) for JSX validation
- **pygls** for the LSP server framework
- **TypeScript** for the VS Code extension
- **No virtual files on disk** — jedi analyzes in-memory, no SegmentManager

---

## Module Map

```
pyxle_langkit/
|-- __init__.py           # Public API exports
|-- server.py             # PyxleLanguageServer (pygls) — main LSP orchestrator
|-- parser_adapter.py     # Tolerant parser wrapper (never raises)
|-- document.py           # PyxDocument — parsed .pyxl with line mapping
|-- workspace.py          # WorkspaceIndex — tracks all .pyxl files
|-- linter.py             # Static analysis: pyflakes, Pyxle rules, React rules
|-- react_checker.py      # Babel-based JSX validation via Node.js
|-- completions.py        # jedi + Pyxle-specific completions
|-- hover.py              # jedi + Pyxle-specific hover docs
|-- definitions.py        # jedi + cross-section go-to-definition
|-- diagnostics.py        # Diagnostic aggregation + position mapping
|-- symbols.py            # Document + workspace symbols
|-- formatting.py         # ruff (Python) + prettier (JSX) coordinator
|-- semantic_tokens.py    # AST-based semantic tokens
|-- cli.py                # CLI: parse, lint, outline, format
+-- js/                   # Node.js helpers
    |-- react_parser_runner.mjs
    +-- jsx_component_extractor.mjs

editors/vscode/           # VS Code extension (TypeScript, thin client)
|-- src/
|   |-- extension.ts      # Activation + LSP client lifecycle
|   +-- status.ts         # Status bar management
|-- syntaxes/
|   +-- pyxl.tmLanguage.json
|-- test/
|   |-- suite/
|   |   +-- extension.test.ts
|   +-- runTest.ts
|-- package.json
|-- tsconfig.json
+-- language-configuration.json
```

---

## Setup

```bash
# Install core framework (editable)
pip install -e /path/to/pyxle[dev]

# Install langkit
pip install -e .[dev]

# Install Babel helpers
npm install

# Build VS Code extension
cd editors/vscode && npm install && npm run compile
```

---

## Mandatory Rules

### 1. Run Tests After Every Change

```bash
pytest                    # Full suite with coverage
pytest tests/ -x          # Stop on first failure
pytest --no-cov           # Skip coverage for faster iteration
```

All tests must pass. Coverage threshold is 95%.

### 2. Test File Convention

```
pyxle_langkit/parser_adapter.py   -> tests/test_parser_adapter.py
pyxle_langkit/document.py         -> tests/test_document.py
pyxle_langkit/linter.py           -> tests/test_linter.py
pyxle_langkit/completions.py      -> tests/test_completions.py
pyxle_langkit/hover.py            -> tests/test_hover.py
pyxle_langkit/definitions.py      -> tests/test_definitions.py
pyxle_langkit/server.py           -> tests/test_server.py
pyxle_langkit/workspace.py        -> tests/test_workspace.py
pyxle_langkit/diagnostics.py      -> tests/test_diagnostics.py
pyxle_langkit/symbols.py          -> tests/test_symbols.py
pyxle_langkit/formatting.py       -> tests/test_formatting.py
pyxle_langkit/semantic_tokens.py  -> tests/test_semantic_tokens.py
pyxle_langkit/cli.py              -> tests/test_cli.py
editors/vscode/                   -> editors/vscode/test/
```

### 3. Lint Before Committing

```bash
ruff check pyxle_langkit/ tests/
```

### 4. Commit Scope Convention

Scope examples: `linter`, `server`, `completions`, `hover`, `definitions`,
`formatting`, `vscode`, `parser`, `cli`, `workspace`.

---

## Module Rules

### Parser Adapter
- Must tolerate incomplete/malformed code — IDE users type incomplete code constantly
- Never raise exceptions for parse failures — return PyxDocument with diagnostics

### Linter
- Python issues use pyflakes — don't reinvent its checks
- `server` and `action` are whitelisted as globals (injected by Pyxle runtime)
- JSX issues delegate to Node.js/Babel
- All issues must have accurate line/column mapped to the original `.pyxl` file

### Completions / Hover / Definitions
- Use jedi for Python intelligence — import safely with try/except
- If jedi is not available, return Pyxle-specific results only (never crash)
- Map all positions: .pyxl → virtual Python → jedi → virtual Python → .pyxl

### LSP Server
- Never block the event loop with synchronous I/O
- Use asyncio.to_thread() for jedi calls (jedi is synchronous)
- Diagnostics published on didOpen, didChange, didSave

### VS Code Extension
- TypeScript — compiled to out/ directory
- Thin LSP client only — no SegmentManager, no virtual files
- All intelligence comes from the LSP server

---

## DO NOT List

- **DO NOT** import anything from `pyxle` other than `pyxle.compiler`
- **DO NOT** add `print()` — use `logging`
- **DO NOT** write virtual files to disk for language analysis
- **DO NOT** use mutable dataclasses for data-carrying types
- **DO NOT** block the async event loop with synchronous I/O
- **DO NOT** grow caches without eviction policies
- **DO NOT** hardcode file paths or OS-specific assumptions
- **DO NOT** commit `.vsix` build artifacts or `node_modules/`
