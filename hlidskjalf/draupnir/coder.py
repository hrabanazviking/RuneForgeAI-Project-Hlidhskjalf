"""Mythic Coder (Slice 25) — code-generation task type.

The Coder turns a :class:`CodeSpec` into files on disk: either the caller
supplies exact file contents, or supplies a ``generator`` hook (template +
LLM interface) that produces the source per file. Every ``.py`` file is
syntax-validated with ``py_compile`` before the file list is returned, so
downstream stages never ingest unparseable code.
"""

from __future__ import annotations

import py_compile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

# generator(name: str, context: dict) -> source code for one file
GeneratorFn = Callable[[str, Dict[str, object]], str]


@dataclass
class CodeSpec:
    """Specification of the code to forge.

    Either ``files`` (exact {relative_path: source}) or ``generator`` with
    ``file_names`` (hook produces each file) — or both, with explicit files
    winning over generated ones on collision.
    """

    name: str
    files: Mapping[str, str] = field(default_factory=dict)
    file_names: List[str] = field(default_factory=list)
    generator: Optional[GeneratorFn] = None
    context: Dict[str, object] = field(default_factory=dict)


class CodegenError(RuntimeError):
    """Raised when generated code is invalid or unwritable."""


class MythicCoder:
    """Forges code files into a workspace directory, syntax-validated."""

    def __init__(self, generator: Optional[GeneratorFn] = None) -> None:
        self.generator = generator

    def generate_code(self, spec: CodeSpec, workspace_dir: str | Path) -> List[str]:
        """Write spec's files under ``workspace_dir``; return relative paths.

        Raises CodegenError if a generator is needed but missing, if a
        ``.py`` file fails syntax validation, or if a path escapes the
        workspace root.
        """
        root = Path(workspace_dir)
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()

        sources: Dict[str, str] = dict(spec.files)
        gen = spec.generator or self.generator
        if spec.file_names and gen is None:
            raise CodegenError(
                f"spec '{spec.name}' needs generated files {spec.file_names} "
                "but no generator hook was provided"
            )
        for name in spec.file_names:
            sources.setdefault(name, gen(name, spec.context))  # type: ignore[arg-type]

        # Pre-validate EVERYTHING before writing anything: source types,
        # relative paths, workspace containment, and .py syntax. A bad
        # file used to be discovered mid-write, leaving a partial forge
        # (valid files — and the broken one — already on disk).
        targets: Dict[str, Path] = {}
        for rel, source in sources.items():
            if not isinstance(rel, str) or not rel:
                raise CodegenError(f"file path must be a non-empty string: {rel!r}")
            if not isinstance(source, str):
                raise CodegenError(
                    f"source for {rel!r} must be a string, got {type(source).__name__}"
                )
            target = (root / Path(rel)).resolve()
            if root not in target.parents and target != root:
                raise CodegenError(f"path escapes workspace: {rel!r}")
            targets[rel] = target
            if target.suffix == ".py":
                try:
                    compile(source, rel, "exec")
                except SyntaxError as exc:
                    raise CodegenError(f"syntax error in {rel}: {exc}") from exc

        written: List[str] = []
        for rel, source in sources.items():
            rel_path = Path(rel)
            target = targets[rel]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source, encoding="utf-8")
            written.append(str(rel_path))
            if target.suffix == ".py":
                self._validate_python(target)

        return sorted(written)

    @staticmethod
    def _validate_python(path: Path) -> None:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            raise CodegenError(f"syntax error in {path.name}: {exc.msg}") from exc
