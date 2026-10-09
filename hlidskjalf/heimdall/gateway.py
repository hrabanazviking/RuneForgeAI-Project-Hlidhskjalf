"""Heimdall gateway: the single ingress pipeline.

``Gateway.handle(raw, credential)`` runs every inbound message through:

    authenticate → parse envelope → validate payload → dispatch

and always returns a well-formed response envelope::

    {"v":1, "id":..., "ts":..., "type":"<req>.response"|"heimdall.error",
     "source":"heimdall", "payload":{"ok": true, "result": {...}},
     "meta":{"correlation_id": <request id>}}

On failure the payload is ``{"ok": false, "error": {"code":...,
"message":..., "details":...}}`` with the matching HTTP status echoed in
``meta.http_status`` for future HTTP transports.

``health_report()`` merges dispatcher, bus, and config health for the
/health endpoint.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Mapping, Optional

from hlidskjalf.common.config import Config, load_config
from hlidskjalf.common.errors import HlidskjalfError
from hlidskjalf.common.logging import bind, get_logger, setup_logging
from hlidskjalf.heimdall.auth import Authenticator
from hlidskjalf.heimdall.dispatch import Dispatcher
from hlidskjalf.heimdall.validate import SchemaRegistry
from hlidskjalf.protocol import envelope as env_mod
from hlidskjalf.protocol.bus import Bus

log = get_logger("heimdall.gateway")


class Gateway:
    """Ingress pipeline: auth → parse → validate → dispatch."""

    def __init__(
        self,
        *,
        config: Optional[Config] = None,
        authenticator: Optional[Authenticator] = None,
        schemas: Optional[SchemaRegistry] = None,
        dispatcher: Optional[Dispatcher] = None,
        bus: Optional[Bus] = None,
    ) -> None:
        self.config = config or load_config()
        setup_logging(
            level=str(self.config.get("logging.level", "INFO")),
            json=bool(self.config.get("logging.json", True)),
        )
        self.auth = authenticator or Authenticator.from_config(self.config)
        self.schemas = schemas or SchemaRegistry()
        self.dispatcher = dispatcher or Dispatcher(
            dead_letter_max=int(self.config.get("heimdall.dead_letter_max", 1000))
        )
        self.bus = bus  # optional: subsystems may share one bus
        self._started_at = time.time()
        self._stats = {"accepted": 0, "rejected": 0}

    # ------------------------------------------------------------------
    # ingress
    # ------------------------------------------------------------------
    def handle(
        self,
        raw: env_mod.RawEnvelope,
        credential: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process one inbound message; always return a response envelope."""
        audit = bind(log, gateway="heimdall")
        try:
            auth_result = self.auth.authenticate(credential)
            env = env_mod.parse(raw)
            audit = bind(log, msg_id=env.get("id"), msg_type=env.get("type"),
                         principal=auth_result.principal)
            self.schemas.validate(env)
            result = self.dispatcher.dispatch(env)
            self._stats["accepted"] += 1
            audit.info("request handled")
            return self._response(env, {"ok": True, "result": result})
        except HlidskjalfError as exc:
            self._stats["rejected"] += 1
            audit.warning("request rejected", code=exc.code, error=exc.message)
            return self._error_response(raw, exc)
        except Exception as exc:  # noqa: BLE001 - never leak a traceback
            self._stats["rejected"] += 1
            audit.warning("request failed unexpectedly", error=str(exc))
            wrapped = HlidskjalfError(f"internal gateway error: {exc}")
            return self._error_response(raw, wrapped)

    def _response(
        self, request: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> Dict[str, Any]:
        msg_type = str(request.get("type", "heimdall"))
        return env_mod.build(
            f"{msg_type}.response",
            payload,
            source="heimdall",
            meta={"correlation_id": request.get("id")},
        )

    def _error_response(
        self, raw: env_mod.RawEnvelope, exc: HlidskjalfError
    ) -> Dict[str, Any]:
        correlation: Optional[str] = None
        if isinstance(raw, Mapping):
            correlation = raw.get("id") if isinstance(raw.get("id"), str) else None
        return env_mod.build(
            "heimdall.error",
            {"ok": False, "error": exc.to_dict()["error"]},
            source="heimdall",
            meta={"correlation_id": correlation, "http_status": exc.http_status},
        )

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def health_report(self) -> Dict[str, Any]:
        """Aggregate health for the /health endpoint."""
        report: Dict[str, Any] = {
            "status": "ok",
            "service": "heimdall",
            "uptime_s": round(time.time() - self._started_at, 3),
            "protocol_version": env_mod.PROTOCOL_VERSION,
            "auth": {"keys_configured": self.auth.key_count},
            "gateway": dict(self._stats),
            "dispatcher": self.dispatcher.health(),
            "schemas": self.schemas.types(),
        }
        if self.bus is not None:
            report["bus"] = self.bus.stats()
        return report
