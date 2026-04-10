"""TypeScript language service bridge.

Manages a Node.js subprocess running the TypeScript language service
worker (``ts_service.mjs``).  Provides completions, hover, and
go-to-definition for JSX sections of ``.pyx`` files.

Communication uses NDJSON over stdin/stdout — the same pattern as
Pyxle's SSR worker pool.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WORKER_SCRIPT = Path(__file__).parent / "js" / "ts_service.mjs"
_REQUEST_TIMEOUT = 5  # seconds


@dataclass(frozen=True, slots=True)
class TsCompletionItem:
    """A completion item from the TypeScript language service."""

    label: str
    kind: str
    sort_text: str
    insert_text: str


@dataclass(frozen=True, slots=True)
class TsQuickInfo:
    """Hover information from the TypeScript language service."""

    display: str
    documentation: str
    kind: str


@dataclass(frozen=True, slots=True)
class TsDefinitionLocation:
    """A definition location from the TypeScript language service."""

    file: str
    line: int       # 0-indexed
    character: int  # 0-indexed


class TypeScriptBridge:
    """Bridge to the TypeScript language service running in Node.js.

    Spawns a single Node.js subprocess and communicates via NDJSON.
    Thread-safe: all requests are serialized via a lock.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._ready = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, project_root: Path | None = None) -> bool:
        """Start the TypeScript service worker.

        Returns True if the worker started and signalled readiness.
        """
        if self._process is not None and self._process.poll() is None:
            return self._ready

        node = _find_node()
        if node is None:
            logger.info("Node.js not found — TypeScript service unavailable")
            return False

        if not _WORKER_SCRIPT.exists():
            logger.warning("ts_service.mjs not found at %s", _WORKER_SCRIPT)
            return False

        # Check that typescript is importable
        ts_check = _find_typescript(project_root)
        if not ts_check:
            logger.info("TypeScript package not found — JSX service unavailable")
            return False

        env = {**os.environ}
        if project_root:
            env["NODE_PATH"] = str(project_root / "node_modules")

        try:
            self._process = subprocess.Popen(
                [node, str(_WORKER_SCRIPT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(project_root) if project_root else None,
                env=env,
            )
        except OSError as exc:
            logger.warning("Failed to start TypeScript service: %s", exc)
            return False

        # Wait for the ready signal.
        try:
            line = self._process.stdout.readline()  # type: ignore[union-attr]
            if line:
                response = json.loads(line)
                if response.get("result", {}).get("ready"):
                    self._ready = True
                    logger.info("TypeScript language service started")
                    return True
        except Exception:
            pass

        logger.warning("TypeScript service did not signal readiness")
        self.stop()
        return False

    def stop(self) -> None:
        """Stop the TypeScript service worker."""
        if self._process is not None:
            try:
                self._process.stdin.close()  # type: ignore[union-attr]
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
            self._ready = False

    @property
    def is_running(self) -> bool:
        """Whether the TypeScript service is running."""
        return (
            self._process is not None
            and self._process.poll() is None
            and self._ready
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_file(
        self,
        pyx_path: Path,
        jsx_content: str,
        project_root: Path | None = None,
    ) -> None:
        """Update the virtual TSX content for a .pyx file."""
        self._send("update", {
            "file": str(pyx_path),
            "content": jsx_content,
            "projectRoot": str(project_root) if project_root else None,
        })

    def remove_file(self, pyx_path: Path) -> None:
        """Remove the virtual TSX content for a .pyx file."""
        self._send("remove", {"file": str(pyx_path)})

    def completions(
        self, pyx_path: Path, line: int, character: int,
    ) -> tuple[TsCompletionItem, ...]:
        """Get completions at a position in the JSX section.

        Line and character are 0-indexed.
        """
        result = self._send("completions", {
            "file": str(pyx_path),
            "line": line,
            "character": character,
        })
        if result is None or "items" not in result:
            return ()

        return tuple(
            TsCompletionItem(
                label=item.get("label", ""),
                kind=item.get("kind", ""),
                sort_text=item.get("sortText", ""),
                insert_text=item.get("insertText", ""),
            )
            for item in result["items"]
        )

    def quick_info(
        self, pyx_path: Path, line: int, character: int,
    ) -> TsQuickInfo | None:
        """Get hover information at a position in the JSX section."""
        result = self._send("quickInfo", {
            "file": str(pyx_path),
            "line": line,
            "character": character,
        })
        if result is None:
            return None

        return TsQuickInfo(
            display=result.get("display", ""),
            documentation=result.get("documentation", ""),
            kind=result.get("kind", ""),
        )

    def definition(
        self, pyx_path: Path, line: int, character: int,
    ) -> tuple[TsDefinitionLocation, ...]:
        """Get definition locations at a position in the JSX section."""
        result = self._send("definition", {
            "file": str(pyx_path),
            "line": line,
            "character": character,
        })
        if result is None or not isinstance(result, list):
            return ()

        return tuple(
            TsDefinitionLocation(
                file=loc.get("file", ""),
                line=loc.get("line", 0),
                character=loc.get("character", 0),
            )
            for loc in result
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _send(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request and wait for the response."""
        with self._lock:
            if not self.is_running:
                return None

            self._request_id += 1
            request = {
                "id": self._request_id,
                "method": method,
                "params": params,
            }

            try:
                line = json.dumps(request) + "\n"
                self._process.stdin.write(line.encode("utf-8"))  # type: ignore[union-attr]
                self._process.stdin.flush()  # type: ignore[union-attr]

                # Read response (blocking, with timeout via alarm).
                response_line = self._process.stdout.readline()  # type: ignore[union-attr]
                if not response_line:
                    logger.debug("TypeScript service returned empty response")
                    return None

                response = json.loads(response_line)
                if "error" in response:
                    logger.debug("TypeScript service error: %s", response["error"])
                    return None

                return response.get("result")
            except Exception:
                logger.debug("TypeScript bridge communication error", exc_info=True)
                return None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _find_node() -> str | None:
    """Find the Node.js binary."""
    # Check common locations
    for path in [
        "/opt/homebrew/bin/node",
        "/usr/local/bin/node",
        "/usr/bin/node",
    ]:
        if os.path.isfile(path):
            return path

    # Check PATH
    import shutil
    return shutil.which("node")


def _find_typescript(project_root: Path | None) -> bool:
    """Check if TypeScript is available."""
    if project_root:
        ts_path = project_root / "node_modules" / "typescript"
        if ts_path.is_dir():
            return True

    # Check global
    node = _find_node()
    if node is None:
        return False

    try:
        result = subprocess.run(
            [node, "-e", "require('typescript')"],
            capture_output=True,
            timeout=5,
            cwd=str(project_root) if project_root else None,
        )
        return result.returncode == 0
    except Exception:
        return False
