"""Layered configuration for Project Hliðskjálf.

Precedence (low → high):
    defaults  <  YAML file (config/hlidskjalf.yaml)  <  environment
    variables (``HLIDSKJALF__SECTION__KEY``)  <  explicit overrides

Law: config, not code. No secrets are ever hardcoded; API keys come from
the YAML file or the environment.

Standard library only — the YAML subset parser below handles the simple
mapping/scalar files Hliðskjálf uses. If PyYAML is ever added, ``load_yaml``
can delegate to it without changing this module's API.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from hlidskjalf.common.errors import ConfigError

ENV_PREFIX = "HLIDSKJALF__"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "hlidskjalf.yaml"

#: Built-in defaults. Everything the runtime reads must have a default here.
DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "logging": {"level": "INFO", "json": True},
    "protocol": {"version": 1},
    "heimdall": {
        "host": "127.0.0.1",
        "port": 8000,
        "auth": {"api_keys": []},
        "dead_letter_max": 1000,
    },
    "hud": {"fps": 60, "width": 1920, "height": 1080},
    "hailo": {"mock": True},
    "kista": {"path": "./data/kista", "encryption": False},
}

#: Dotted keys whose values are secrets and must be redacted in reports.
SECRET_KEYS = {"heimdall.auth.api_keys"}


# ---------------------------------------------------------------------------
# minimal YAML reader (simple mapping/scalar subset)
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    """Strip a trailing ``#`` comment, honouring quoted strings.

    A ``#`` inside single or double quotes is part of the value, e.g.
    ``greeting: "hello # not a comment"`` keeps the ``#``. Handles
    backslash escapes inside double quotes.
    """
    out: List[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote is not None:
            out.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in inner.split(",")]
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def load_yaml(path: str | Path) -> Dict[str, Any]:
    """Load a simple YAML mapping file (stdlib-only subset).

    Supports nested mappings via indentation and scalar values, lists via
    ``- item`` lines, comments, and quoted strings. Raises :class:`ConfigError`
    on unreadable files.
    """
    try:
        raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc

    items: List[Tuple[int, str]] = []
    for raw in raw_lines:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        items.append((indent, line.strip()))

    node, _ = _parse_block(items, 0, -1)
    if node is None:
        return {}
    if not isinstance(node, dict):
        raise ConfigError(f"config root must be a mapping in {path}")
    return node


def _parse_block(
    items: List[Tuple[int, str]], index: int, parent_indent: int
) -> Tuple[Any, int]:
    """Parse one indented block; return (node, next_index)."""
    node: Any = None
    i = index
    while i < len(items):
        indent, text = items[i]
        if indent <= parent_indent:
            break
        if text.startswith("- "):
            if node is None:
                node = []
            if not isinstance(node, list):
                raise ConfigError(f"mixed mapping/list block near: {text!r}")
            node.append(_parse_scalar(text[2:]))
            i += 1
        elif ":" in text:
            if node is None:
                node = {}
            if not isinstance(node, dict):
                raise ConfigError(f"mixed mapping/list block near: {text!r}")
            key, _, rest = text.partition(":")
            key, rest = key.strip(), rest.strip()
            if rest:
                node[key] = _parse_scalar(rest)
                i += 1
            else:
                child, i = _parse_block(items, i + 1, indent)
                node[key] = child if child is not None else {}
        else:
            raise ConfigError(f"cannot parse config line: {text!r}")
    return node, i


# ---------------------------------------------------------------------------
# layering helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: Dict[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _set_dotted(tree: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split("__") if "__" in dotted else dotted.split(".")
    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _get_dotted(tree: Mapping[str, Any], dotted: str) -> Any:
    node: Any = tree
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _coerce(value: str, template: Any, var_name: str = "(unknown)") -> Any:
    """Coerce an env-var string to the type of the default it overrides.

    Raises:
        ConfigError: The value cannot be coerced to the expected type; the
            message names the offending environment variable.
    """
    if isinstance(template, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(template, int):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"env var {var_name} must be an int, got {value!r}",
                details={"var": var_name, "value": value},
            ) from exc
    if isinstance(template, float):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"env var {var_name} must be a float, got {value!r}",
                details={"var": var_name, "value": value},
            ) from exc
    if isinstance(template, list):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _env_overrides() -> Dict[str, Any]:
    """Collect ``HLIDSKJALF__A__B`` env vars as a nested dict."""
    tree: Dict[str, Any] = {}
    for name, value in os.environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        dotted = name[len(ENV_PREFIX):].lower().replace("__", ".")
        template = _get_dotted(DEFAULTS, dotted)
        _set_dotted(tree, dotted, _coerce(value, template, name))
    return tree


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _check(condition: bool, message: str, problems: List[str]) -> None:
    if not condition:
        problems.append(message)


def _is_int(value: Any) -> bool:
    """True for real ints; ``bool`` is an int subclass and must not pass."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate(cfg: Mapping[str, Any]) -> None:
    """Validate an effective config; raise :class:`ConfigError` on problems."""
    problems: List[str] = []
    version = cfg.get("version")
    _check(_is_int(version) and version == 1,
           f"'version' must be 1, got {version!r}", problems)

    log_level = str(_get_dotted(cfg, "logging.level") or "").upper()
    _check(log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
           f"'logging.level' invalid: {log_level!r}", problems)

    port = _get_dotted(cfg, "heimdall.port")
    _check(_is_int(port) and 1 <= port <= 65535,
           f"'heimdall.port' must be 1-65535, got {port!r}", problems)

    fps = _get_dotted(cfg, "hud.fps")
    _check(_is_int(fps) and fps > 0,
           f"'hud.fps' must be a positive int, got {fps!r}", problems)

    keys = _get_dotted(cfg, "heimdall.auth.api_keys")
    _check(isinstance(keys, list) and all(isinstance(k, str) for k in keys),
           "'heimdall.auth.api_keys' must be a list of strings", problems)

    dlm = _get_dotted(cfg, "heimdall.dead_letter_max")
    _check(_is_int(dlm) and dlm > 0,
           f"'heimdall.dead_letter_max' must be positive, got {dlm!r}", problems)

    if problems:
        raise ConfigError("invalid configuration", details={"problems": problems})


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

class Config:
    """The effective, validated configuration."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data: Dict[str, Any] = copy.deepcopy(dict(data))

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a value by dotted path, e.g. ``cfg.get("heimdall.port")``."""
        value = _get_dotted(self._data, dotted)
        return default if value is None else value

    def as_dict(self) -> Dict[str, Any]:
        """Return a deep copy of the effective configuration."""
        return copy.deepcopy(self._data)

    def report(self) -> Dict[str, Any]:
        """Effective config safe to log/display: secrets are redacted."""
        data = self.as_dict()
        for dotted in SECRET_KEYS:
            if _get_dotted(data, dotted) is not None:
                _set_dotted(data, dotted, ["***redacted***"])
        return data


def load_config(
    path: Optional[str | Path] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> Config:
    """Load and validate the layered configuration.

    Args:
        path: YAML file path; defaults to ``config/hlidskjalf.yaml``
            (skipped silently when missing).
        overrides: Highest-precedence explicit overrides (nested dict).

    Raises:
        ConfigError: The merged configuration failed validation.
    """
    merged: Dict[str, Any] = copy.deepcopy(DEFAULTS)
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        merged = _deep_merge(merged, load_yaml(cfg_path))
    merged = _deep_merge(merged, _env_overrides())
    if overrides:
        merged = _deep_merge(merged, overrides)
    validate(merged)
    return Config(merged)
