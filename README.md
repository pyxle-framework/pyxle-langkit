# Pyxle Language Toolkit

Language tools for [Pyxle](https://pyxle.dev) `.pyxl` files — LSP server, linter, and VS Code extension.

## Features

- **Syntax highlighting** via TextMate grammar (Python + JSX sections)
- **Diagnostics** from pyflakes, Babel, and Pyxle-specific rules
- **Completions** via Jedi (Python) + Pyxle component completions (JSX)
- **Hover documentation** for Python symbols and Pyxle decorators/components
- **Go-to-definition** via Jedi with cross-section navigation
- **Document symbols** and **workspace symbols**
- **Formatting** with ruff (Python) and prettier (JSX)
- **Semantic tokens** via AST analysis

## Installation

```bash
pip install pyxle-langkit
```

The VS Code extension is available on the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=pyxle.pyxle-language-tools).

## Architecture

All intelligence lives in the Python LSP server. The VS Code extension is a thin LSP client (~100 LOC).

- **Python analysis**: [Jedi](https://jedi.readthedocs.io/) for completions, definitions, hover
- **JSX analysis**: Pyxle-specific completions + [Babel](https://babeljs.io/) for validation
- **Static analysis**: [pyflakes](https://pypi.org/project/pyflakes/) + Pyxle rules
- **LSP framework**: [pygls](https://pygls.readthedocs.io/)

## License

MIT
