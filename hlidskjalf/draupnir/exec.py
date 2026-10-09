"""Aesir Exec (Slice 26) — sandboxed Python execution.

Runs caller-supplied code in a fresh subprocess with OS resource limits
(CPU time, address-space size via the ``resource`` module) and a wall-clock
timeout. Dangerous patterns (networking, process spawning, shell escapes)
are blocked by an AST pre-scan before execution.

Limitations (documented, not fixable in pure stdlib):
- Network isolation is best-effort: the pre-scan blocks the obvious
  imports (``socket``, ``urllib``, ``http``, ``requests``, ...), but a truly
  adversarial payload could still reach the network via exotic paths. Full
  isolation requires seccomp/net-namespaces/containers.
- ``resource`` rlimits are POSIX-only; on platforms without the
  ``resource`` module, CPU/memory limits are skipped (flagged in result).
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import Optional

try:  # POSIX-only
    import resource
except ImportError:  # pragma: no cover - non-POSIX
    resource = None  # type: ignore[assignment]

# Modules whose import is flat-out refused by the pre-scan.
BLOCKED_IMPORTS = frozenset(
    {
        "socket",
        "socketserver",
        "http",
        "urllib",
        "requests",
        "ftplib",
        "telnetlib",
        "smtplib",
        "ssl",
        "asyncio",
        "multiprocessing",
        "subprocess",
        "pty",
        "signal",
        "ctypes",
    }
)

# Attribute-style escapes, checked as dotted names (os.system, etc.).
BLOCKED_ATTRS = frozenset(
    {
        "os.system",
        "os.popen",
        "os.execv",
        "os.execve",
        "os.execl",
        "os.execlp",
        "os.execvp",
        "os.execvpe",
        "os.fork",
        "os.kill",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "sys.exit",
        "builtins.eval",
        "builtins.exec",
    }
)

DEFAULT_CPU_LIMIT = 5          # seconds of CPU time
DEFAULT_MEM_LIMIT = 256 * 1024 * 1024  # bytes of address space


class BlockedCodeError(ValueError):
    """Raised when the pre-scan refuses to run the code."""


@dataclass
class ExecResult:
    """Outcome of a sandboxed run."""

    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    duration_s: float
    rlimits_applied: bool
    blocked: bool = False
    blocked_reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked and not self.timed_out and self.returncode == 0


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return ".".join(parts)


class _SafetyScanner(ast.NodeVisitor):
    """Rejects networking / process-spawning / shell-escape constructs."""

    def __init__(self) -> None:
        self.reason: Optional[str] = None

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in BLOCKED_IMPORTS:
                self.reason = f"blocked import: {alias.name!r}"
                return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module:
            root = node.module.split(".")[0]
            if root in BLOCKED_IMPORTS:
                self.reason = f"blocked import: from {node.module!r}"
                return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        dotted = _dotted_name(node.func)
        if dotted in BLOCKED_ATTRS:
            self.reason = f"blocked call: {dotted}"
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "__import__":
            self.reason = "blocked call: __import__"
        self.generic_visit(node)


def prescan(code: str) -> Optional[str]:
    """Return a block reason for unsafe code, or None if it passes.

    Also refuses code that fails to parse.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc}"
    scanner = _SafetyScanner()
    scanner.visit(tree)
    return scanner.reason


def _apply_rlimits(cpu_limit: int, mem_limit: int) -> None:
    """preexec_fn: harden the child before exec."""

    def _inner() -> None:
        if resource is None:
            return
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
        resource.setrlimit(resource.RLIMIT_AS, (mem_limit, mem_limit))

    return _inner


def run_python(
    code: str,
    timeout: float = 5.0,
    cpu_limit: int = DEFAULT_CPU_LIMIT,
    mem_limit: int = DEFAULT_MEM_LIMIT,
    raise_on_block: bool = True,
) -> ExecResult:
    """Execute ``code`` in a sandboxed subprocess.

    - AST pre-scan blocks dangerous patterns (BlockedCodeError if
      ``raise_on_block``, else an ExecResult with blocked=True).
    - Child runs with RLIMIT_CPU / RLIMIT_AS and a wall-clock ``timeout``.
    - Captures stdout, stderr, returncode; never raises for the code's own
      failures.
    """
    code = textwrap.dedent(code)
    reason = prescan(code)
    if reason is not None:
        if raise_on_block:
            raise BlockedCodeError(reason)
        return ExecResult(
            stdout="",
            stderr="",
            returncode=-1,
            timed_out=False,
            duration_s=0.0,
            rlimits_applied=False,
            blocked=True,
            blocked_reason=reason,
        )

    rlimits_applied = resource is not None
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_apply_rlimits(cpu_limit, mem_limit),
        )
        timed_out = False
        rc = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = -1
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = time.monotonic() - start
    return ExecResult(
        stdout=out,
        stderr=err,
        returncode=rc,
        timed_out=timed_out,
        duration_s=duration,
        rlimits_applied=rlimits_applied,
    )
