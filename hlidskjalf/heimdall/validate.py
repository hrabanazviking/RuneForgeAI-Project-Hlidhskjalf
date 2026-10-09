"""Heimdall payload schema validation.

Inbound envelope payloads are checked against per-message-type schemas
*before* dispatch. Schemas are plain dicts (stdlib-only mini DSL)::

    {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "min_length": 1},
            "level": {"type": "integer", "min": 1, "max": 10},
            "tags": {"type": "array", "items": {"type": "string"}},
            "mode": {"type": "string", "enum": ["fast", "slow"]},
            "addr": {"type": "string", "pattern": r"^\\d+\\.\\d+"},
        },
    }

Supported field types: string, integer, number, boolean, array, object,
null. Constraints: required, enum, min/max (numeric), min_length/max_length
(strings), pattern (regex), items (array elements), properties + required
(nested objects).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from hlidskjalf.common.errors import ValidationError
from hlidskjalf.common.logging import bind, get_logger

log = get_logger("heimdall.validate")

_TYPES = ("string", "integer", "number", "boolean", "array", "object", "null")


def _check_type(value: Any, want: str) -> bool:
    if want == "string":
        return isinstance(value, str)
    if want == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "boolean":
        return isinstance(value, bool)
    if want == "array":
        return isinstance(value, list)
    if want == "object":
        return isinstance(value, Mapping)
    if want == "null":
        return value is None
    return False


def _validate_value(value: Any, spec: Mapping[str, Any], path: str) -> List[str]:
    problems: List[str] = []
    want = spec.get("type")
    if want is not None:
        if want not in _TYPES:
            problems.append(f"{path}: unknown schema type {want!r}")
            return problems
        if not _check_type(value, want):
            problems.append(
                f"{path}: expected {want}, got {type(value).__name__}"
            )
            return problems

    enum = spec.get("enum")
    if enum is not None and value not in enum:
        problems.append(f"{path}: {value!r} not in {enum!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.get("min") is not None and value < spec["min"]:
            problems.append(f"{path}: {value!r} < min {spec['min']!r}")
        if spec.get("max") is not None and value > spec["max"]:
            problems.append(f"{path}: {value!r} > max {spec['max']!r}")

    if isinstance(value, str):
        if spec.get("min_length") is not None and len(value) < spec["min_length"]:
            problems.append(f"{path}: shorter than min_length {spec['min_length']}")
        if spec.get("max_length") is not None and len(value) > spec["max_length"]:
            problems.append(f"{path}: longer than max_length {spec['max_length']}")
        pattern = spec.get("pattern")
        if pattern and not re.search(pattern, value):
            problems.append(f"{path}: does not match pattern {pattern!r}")

    if isinstance(value, list) and "items" in spec:
        item_spec = spec["items"]
        if isinstance(item_spec, Mapping):
            for idx, item in enumerate(value):
                problems.extend(
                    _validate_value(item, item_spec, f"{path}[{idx}]")
                )

    if isinstance(value, Mapping) and "properties" in spec:
        props = spec["properties"]
        required = spec.get("required", [])
        for name in required:
            if name not in value:
                problems.append(f"{path}.{name}: required field missing")
        for name, sub in props.items():
            if name in value and isinstance(sub, Mapping):
                problems.extend(_validate_value(value[name], sub, f"{path}.{name}"))
    return problems


class SchemaRegistry:
    """Maps message types to payload schemas.

    Open by default: types without a schema pass validation. Pass
    ``closed=True`` to reject unregistered types, or call :meth:`require`
    per type.
    """

    def __init__(self, closed: bool = False) -> None:
        self._schemas: Dict[str, Dict[str, Any]] = {}
        self._closed = closed
        self._required_closed: set[str] = set()

    def require(self, msg_type: str) -> None:
        """Reject ``msg_type`` unless it has a registered schema."""
        self._required_closed.add(msg_type)

    def register(self, msg_type: str, schema: Mapping[str, Any]) -> None:
        """Register (or replace) the schema for ``msg_type``."""
        if not isinstance(schema, Mapping):
            raise ValidationError("schema must be a mapping",
                                  details={"msg_type": msg_type})
        self._schemas[msg_type] = dict(schema)

    def has(self, msg_type: str) -> bool:
        """True when a schema exists for ``msg_type``."""
        return msg_type in self._schemas

    def types(self) -> List[str]:
        """Registered message types."""
        return sorted(self._schemas)

    def reasons(self, envelope: Mapping[str, Any]) -> List[str]:
        """Return human-readable problems; empty list means valid.

        Messages with no registered schema pass (open by default); use
        :meth:`require` to flip a type to closed.
        """
        msg_type = str(envelope.get("type", ""))
        schema = self._schemas.get(msg_type)
        if schema is None:
            if self._closed or msg_type in self._required_closed:
                return [f"no schema registered for message type {msg_type!r}"]
            return []
        payload = envelope.get("payload", {})
        if not isinstance(payload, Mapping):
            return ["payload: must be a JSON object"]
        return _validate_value(payload, schema, "payload")

    def validate(self, envelope: Mapping[str, Any]) -> None:
        """Validate an envelope's payload; raise :class:`ValidationError`.

        Raises:
            ValidationError: With ``details={"reasons": [...]}``.
        """
        msg_type = str(envelope.get("type", ""))
        problems = self.reasons(envelope)
        audit = bind(log, msg_type=msg_type)
        if problems:
            audit.warning("payload rejected", reasons=problems)
            raise ValidationError(
                f"payload failed schema validation for {msg_type!r}",
                details={"msg_type": msg_type, "reasons": problems},
            )
        if self.has(msg_type):
            audit.debug("payload accepted")
