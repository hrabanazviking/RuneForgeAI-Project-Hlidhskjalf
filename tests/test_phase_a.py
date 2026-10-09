"""Phase A (slices 1-8) tests: foundation of Project Hliðskjálf.

Slices:
  1. monorepo skeleton (pyproject / Makefile / pytest collection)
  2. common: logging, errors, ids
  3. common: config (layered defaults < yaml < env < overrides)
  4. protocol: versioned JSON envelope
  5. protocol: in-process pub/sub + req/rep bus
  6. heimdall: API-key auth
  7. heimdall: payload schema validation
  8. heimdall: dispatcher + gateway + health
"""

import io
import json
import logging
import os
import time

import pytest

from hlidskjalf.common import config as config_mod
from hlidskjalf.common import errors, ids
from hlidskjalf.common import logging as hlog
from hlidskjalf.common.config import Config, load_config
from hlidskjalf.heimdall import Authenticator, Dispatcher, Gateway, SchemaRegistry
from hlidskjalf.protocol import Bus, envelope


# ---------------------------------------------------------------------------
# Slice 1: skeleton
# ---------------------------------------------------------------------------

class TestSkeleton:
    def test_package_imports(self):
        import hlidskjalf
        import hlidskjalf.common
        import hlidskjalf.protocol
        import hlidskjalf.heimdall
        assert hlidskjalf is not None

    def test_makefile_has_test_target(self):
        from pathlib import Path
        makefile = Path(__file__).resolve().parents[1] / "Makefile"
        text = makefile.read_text()
        assert "test:" in text and "pytest" in text


# ---------------------------------------------------------------------------
# Slice 2a: logging
# ---------------------------------------------------------------------------

class TestLogging:
    def test_json_log_line(self):
        stream = io.StringIO()
        hlog.setup_logging(level="INFO", stream=stream, force=True)
        log = hlog.bind(hlog.get_logger("test.unit"), request_id="r1")
        log.info("hello")
        line = stream.getvalue().strip().splitlines()[-1]
        entry = json.loads(line)
        assert entry["level"] == "INFO"
        assert entry["msg"] == "hello"
        assert entry["logger"] == "hlidskjalf.test.unit"
        assert entry["context"]["request_id"] == "r1"
        assert "ts" in entry

    def test_get_logger_returns_stdlib_logger(self):
        # Contract relied upon by other phases (kista/_compat.py).
        assert isinstance(hlog.get_logger("x"), logging.Logger)

    def test_bind_injects_context(self):
        stream = io.StringIO()
        hlog.setup_logging(level="DEBUG", stream=stream, force=True)
        log = hlog.bind(hlog.get_logger("test.bind"), trace="t9")
        log.debug("bound", extra_field=1)
        entry = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert entry["context"]["trace"] == "t9"
        assert entry["context"]["extra_field"] == 1

    def test_levels_respected(self):
        stream = io.StringIO()
        hlog.setup_logging(level="WARNING", stream=stream, force=True)
        log = hlog.get_logger("test.levels")
        log.info("suppressed")
        log.warning("shown")
        lines = stream.getvalue().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["msg"] == "shown"

    def test_exception_logged(self):
        stream = io.StringIO()
        hlog.setup_logging(level="ERROR", stream=stream, force=True)
        log = hlog.get_logger("test.exc")
        try:
            raise ValueError("boom")
        except ValueError:
            log.error("failed", exc_info=True)
        entry = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert "ValueError: boom" in entry["exc"]


# ---------------------------------------------------------------------------
# Slice 2b: errors
# ---------------------------------------------------------------------------

class TestErrors:
    def test_taxonomy_codes_and_status(self):
        cases = [
            (errors.AuthError, "auth_failed", 401),
            (errors.ValidationError, "validation_failed", 400),
            (errors.NotFoundError, "not_found", 404),
            (errors.ConflictError, "conflict", 409),
            (errors.StorageError, "storage_failed", 500),
            (errors.ConfigError, "config_invalid", 500),
            (errors.ProtocolError, "protocol_error", 400),
            (errors.BusError, "bus_error", 503),
            (errors.DispatchError, "dispatch_failed", 500),
            (errors.TimeoutError, "timeout", 504),
            (errors.RateLimitError, "rate_limited", 429),
        ]
        for cls, code, status in cases:
            err = cls("msg")
            assert isinstance(err, errors.HlidskjalfError)
            assert err.code == code
            assert err.http_status == status

    def test_to_dict_shape(self):
        err = errors.NotFoundError("missing", details={"id": "x"})
        d = err.to_dict()
        assert d == {"error": {"code": "not_found", "message": "missing",
                              "details": {"id": "x"}}}
        json.dumps(d)  # must be JSON-serializable

    def test_roundtrip_from_dict(self):
        original = errors.RateLimitError("slow down", details={"retry": 5})
        rebuilt = errors.error_from_dict(original.to_dict())
        assert isinstance(rebuilt, errors.RateLimitError)
        assert rebuilt.code == "rate_limited"
        assert rebuilt.message == "slow down"
        assert rebuilt.details == {"retry": 5}

    def test_unknown_code_falls_back_to_base(self):
        rebuilt = errors.error_from_dict({"error": {"code": "nope"}})
        assert type(rebuilt) is errors.HlidskjalfError

    def test_catchable_as_exception(self):
        with pytest.raises(errors.HlidskjalfError):
            raise errors.ValidationError("bad")


# ---------------------------------------------------------------------------
# Slice 2c: ids
# ---------------------------------------------------------------------------

class TestIds:
    def test_new_id_shape(self):
        value = ids.new_id()
        assert isinstance(value, str)
        assert len(value) == 26
        assert ids.is_valid(value)

    def test_unique(self):
        seen = {ids.new_id() for _ in range(1000)}
        assert len(seen) == 1000

    def test_sortable_chronological(self):
        first = ids.new_id()
        time.sleep(0.002)
        second = ids.new_id()
        assert first < second

    def test_monotonic_within_same_ms(self):
        batch = [ids.new_id(at=1_700_000_000.0) for _ in range(100)]
        assert batch == sorted(batch)
        assert len(set(batch)) == 100

    def test_timestamp_extraction(self):
        # Use a future timestamp: ids are monotonic, so an older fixed
        # timestamp could be clamped up to the last-seen millisecond.
        at = time.time() + 3600.123
        value = ids.new_id(at=at)
        assert ids.timestamp_ms(value) == int(at * 1000)
        assert abs(ids.timestamp(value).timestamp() - at) < 0.001

    def test_invalid_rejected(self):
        assert not ids.is_valid("not-an-id")
        assert not ids.is_valid("0" * 25)
        assert not ids.is_valid("")
        with pytest.raises(ValueError):
            ids.timestamp_ms("bogus")


# ---------------------------------------------------------------------------
# Slice 3: config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_load(self):
        cfg = load_config(path="/nonexistent/path.yaml")
        assert cfg.get("heimdall.port") == 8000
        assert cfg.get("logging.level") == "INFO"
        assert cfg.get("heimdall.auth.api_keys") == []

    def test_yaml_layer(self, tmp_path):
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(
            "heimdall:\n  port: 9001\n  auth:\n    api_keys:\n      - key-a\n")
        cfg = load_config(path=yaml_file)
        assert cfg.get("heimdall.port") == 9001
        assert cfg.get("heimdall.auth.api_keys") == ["key-a"]
        assert cfg.get("heimdall.host") == "127.0.0.1"  # default survives

    def test_env_layer(self, monkeypatch):
        monkeypatch.setenv("HLIDSKJALF__HEIMDALL__PORT", "9002")
        monkeypatch.setenv("HLIDSKJALF__LOGGING__LEVEL", "debug")
        monkeypatch.setenv("HLIDSKJALF__HEIMDALL__AUTH__API_KEYS", "a,b")
        cfg = load_config(path="/nonexistent/path.yaml")
        assert cfg.get("heimdall.port") == 9002
        assert cfg.get("logging.level") == "debug"
        assert cfg.get("heimdall.auth.api_keys") == ["a", "b"]

    def test_overrides_win(self, monkeypatch):
        monkeypatch.setenv("HLIDSKJALF__HEIMDALL__PORT", "9002")
        cfg = load_config(path="/nonexistent/path.yaml",
                          overrides={"heimdall": {"port": 7000}})
        assert cfg.get("heimdall.port") == 7000

    def test_validation_rejects_bad_values(self):
        with pytest.raises(errors.ConfigError):
            load_config(path="/nonexistent/path.yaml",
                        overrides={"heimdall": {"port": 99999}})
        with pytest.raises(errors.ConfigError):
            load_config(path="/nonexistent/path.yaml",
                        overrides={"logging": {"level": "LOUD"}})

    def test_report_redacts_secrets(self):
        cfg = load_config(path="/nonexistent/path.yaml",
                          overrides={"heimdall": {"auth": {"api_keys": ["s3cr3t"]}}})
        report = cfg.report()
        assert report["heimdall"]["auth"]["api_keys"] == ["***redacted***"]
        assert cfg.get("heimdall.auth.api_keys") == ["s3cr3t"]  # real value intact

    def test_real_config_file_loads(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "config" / "hlidskjalf.yaml"
        if path.exists():
            cfg = load_config(path=path)
            assert cfg.get("version") == 1

    def test_yaml_subset_parser(self, tmp_path):
        yaml_file = tmp_path / "t.yaml"
        yaml_file.write_text(
            "# comment\n"
            "a: 1\n"
            "b: true\n"
            "c: 2.5\n"
            "d: \"quoted\"\n"
            "e: []\n"
            "f:\n"
            "  - x\n"
            "  - y\n"
            "g:\n"
            "  h: nested\n")
        data = config_mod.load_yaml(yaml_file)
        assert data == {"a": 1, "b": True, "c": 2.5, "d": "quoted",
                        "e": [], "f": ["x", "y"], "g": {"h": "nested"}}

    def test_config_is_dict_like(self):
        cfg = load_config(path="/nonexistent/path.yaml")
        assert cfg.get("missing.key", "fallback") == "fallback"
        d = cfg.as_dict()
        d["heimdall"]["port"] = 1
        assert cfg.get("heimdall.port") == 8000  # copy, not alias


# ---------------------------------------------------------------------------
# Slice 4: envelope
# ---------------------------------------------------------------------------

class TestEnvelope:
    def test_build_defaults(self):
        env = envelope.build("host.state", {"cpu": 0.5}, source="heimdall")
        assert env["v"] == envelope.PROTOCOL_VERSION == 1
        assert ids.is_valid(env["id"])
        assert isinstance(env["ts"], float)
        assert env["type"] == "host.state"
        assert env["payload"] == {"cpu": 0.5}
        assert env["meta"] == {}

    def test_build_meta_and_id(self):
        env = envelope.build("a.b", {}, source="s", meta={"trace_id": "t"},
                             msg_id=ids.new_id(), ts=123.0)
        assert env["meta"] == {"trace_id": "t"}
        assert env["ts"] == 123.0

    def test_roundtrip(self):
        env = envelope.build("wyrd.event", {"n": 1}, source="wyrd")
        assert envelope.parse(envelope.dumps(env)) == env
        assert envelope.parse(env) == env  # dict input also fine

    def test_rejects_bad_version(self):
        env = envelope.build("a.b", {}, source="s")
        env["v"] = 99
        with pytest.raises(errors.ProtocolError) as exc_info:
            envelope.parse(env)
        assert exc_info.value.details["supported"] == [1]

    def test_rejects_malformed(self):
        with pytest.raises(errors.ValidationError):
            envelope.parse("{not json")
        with pytest.raises(errors.ValidationError):
            envelope.parse('{"v": 1}')  # missing fields
        with pytest.raises(errors.ValidationError):
            envelope.parse(42)

    def test_rejects_bad_type_name(self):
        with pytest.raises(errors.ValidationError):
            envelope.build("Bad Type!", {}, source="s")

    def test_rejects_bad_payload(self):
        with pytest.raises(errors.ValidationError):
            envelope.build("a.b", ["not", "a", "dict"], source="s")

    def test_reply_helpers(self):
        req = envelope.build("svc.ping", {}, source="c",
                             meta={"correlation_id": "c1"})
        rep = envelope.build("svc.ping.reply", {"ok": True}, source="s",
                             meta={"is_reply": True, "correlation_id": "c1"})
        assert envelope.is_reply(rep)
        assert not envelope.is_reply(req)
        assert envelope.correlation_id(rep) == "c1"
        assert envelope.correlation_id(req) == "c1"

    def test_schema_file_exists_and_matches(self):
        from pathlib import Path
        schema_path = (Path(__file__).resolve().parents[1]
                       / "hlidskjalf" / "protocol" / "schemas" / "envelope.json")
        assert schema_path.exists()
        schema = json.loads(schema_path.read_text())
        assert schema["properties"]["v"]["enum"] == [1]
        assert "id" in schema["required"]


# ---------------------------------------------------------------------------
# Slice 5: bus
# ---------------------------------------------------------------------------

class TestBus:
    def test_pubsub(self):
        bus = Bus("t-pubsub")
        try:
            received = []
            bus.subscribe("host.state", lambda e: received.append(e))
            env = bus.publish("host.state", {"cpu": 1}, source="t")
            deadline = time.time() + 2
            while not received and time.time() < deadline:
                time.sleep(0.01)
            assert len(received) == 1
            assert received[0]["id"] == env["id"]
            assert received[0]["payload"] == {"cpu": 1}
        finally:
            bus.stop()

    def test_multiple_subscribers_and_unsubscribe(self):
        bus = Bus("t-multi")
        try:
            a, b = [], []
            unsub = bus.subscribe("x.y", lambda e: a.append(e))
            bus.subscribe("x.y", lambda e: b.append(e))
            bus.publish("x.y", {})
            time.sleep(0.3)
            assert len(a) == 1 and len(b) == 1
            unsub()
            bus.publish("x.y", {})
            time.sleep(0.3)
            assert len(a) == 1 and len(b) == 2
        finally:
            bus.stop()

    def test_wildcard(self):
        bus = Bus("t-wild")
        try:
            seen = []
            bus.subscribe("host.*", lambda e: seen.append(e["type"]))
            bus.publish("host.vitals", {})
            bus.publish("other.thing", {})
            time.sleep(0.3)
            assert seen == ["host.vitals"]
        finally:
            bus.stop()

    def test_request_reply(self):
        bus = Bus("t-reqrep")
        try:
            bus.subscribe("svc.echo",
                          lambda e: bus.reply(e, {"echo": e["payload"]}, source="svc"))
            reply = bus.request("svc.echo", {"n": 7}, timeout=2.0)
            assert reply["payload"] == {"echo": {"n": 7}}
            assert envelope.is_reply(reply)
        finally:
            bus.stop()

    def test_request_timeout(self):
        bus = Bus("t-timeout")
        try:
            with pytest.raises(errors.TimeoutError):
                bus.request("nobody.listens", {}, timeout=0.2)
        finally:
            bus.stop()

    def test_handler_error_does_not_kill_bus(self):
        bus = Bus("t-err")
        try:
            def bad(env):
                raise RuntimeError("handler boom")
            good = []
            bus.subscribe("z.z", bad)
            bus.subscribe("z.z", lambda e: good.append(e))
            bus.publish("z.z", {})
            time.sleep(0.3)
            assert len(good) == 1
            assert bus.stats()["handler_errors"] == 1
        finally:
            bus.stop()

    def test_stats(self):
        bus = Bus("t-stats")
        try:
            stats = bus.stats()
            assert stats["name"] == "t-stats"
            assert stats["running"] is False  # lazy start on first use
            bus.start()
            assert bus.stats()["running"] is True
            assert bus.stats()["published"] == 0
        finally:
            bus.stop()

    def test_latency_local(self):
        bus = Bus("t-lat")
        try:
            latencies = []
            bus.subscribe("p.p", lambda e: latencies.append(
                time.time() - e["ts"]))
            for _ in range(20):
                bus.publish("p.p", {})
            time.sleep(0.5)
            assert latencies
            assert sum(latencies) / len(latencies) < 0.05  # well under budget
        finally:
            bus.stop()


# ---------------------------------------------------------------------------
# Slice 6: auth
# ---------------------------------------------------------------------------

class TestAuth:
    def _auth(self):
        return Authenticator(["muse:muse-key", "plain-key"])

    def test_accept_valid_key(self):
        result = self._auth().authenticate("muse-key")
        assert result.ok
        assert result.principal == "muse"

    def test_accept_plain_key(self):
        result = self._auth().authenticate("plain-key")
        assert result.ok
        assert result.principal.startswith("key:")

    def test_reject_wrong_key(self):
        with pytest.raises(errors.AuthError):
            self._auth().authenticate("wrong")

    def test_reject_missing(self):
        with pytest.raises(errors.AuthError):
            self._auth().authenticate(None)
        with pytest.raises(errors.AuthError):
            self._auth().authenticate("   ")

    def test_fail_closed_without_keys(self):
        with pytest.raises(errors.AuthError) as exc_info:
            Authenticator([]).authenticate("anything")
        assert exc_info.value.details["reason"] == "not_configured"

    def test_from_config(self):
        cfg = load_config(path="/nonexistent/path.yaml",
                          overrides={"heimdall": {"auth": {"api_keys": ["a:b"]}}})
        auth = Authenticator.from_config(cfg)
        assert auth.key_count == 1
        assert auth.authenticate("b").principal == "a"

    def test_fingerprint_is_not_reversible(self):
        from hlidskjalf.heimdall.auth import fingerprint
        fp = fingerprint("secret")
        assert fp != "secret" and len(fp) == 12

    def test_bearer_extraction(self):
        auth = self._auth()
        assert auth.extract_bearer({"Authorization": "Bearer muse-key"}) == "muse-key"
        assert auth.extract_bearer({"authorization": "Bearer muse-key"}) == "muse-key"
        assert auth.extract_bearer({}) is None

    def test_no_timing_leak_on_compare(self):
        # constant-time compare must at least not raise on odd inputs
        auth = self._auth()
        with pytest.raises(errors.AuthError):
            auth.authenticate("muse-key-extra-long-padding" * 10)


# ---------------------------------------------------------------------------
# Slice 7: validation
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["method"],
    "properties": {
        "method": {"type": "string", "enum": ["tarot", "runes"]},
        "count": {"type": "integer", "min": 1, "max": 10},
        "name": {"type": "string", "min_length": 1, "max_length": 40},
        "tags": {"type": "array", "items": {"type": "string"}},
        "nested": {
            "type": "object",
            "required": ["x"],
            "properties": {"x": {"type": "number"}},
        },
    },
}


class TestValidation:
    def _registry(self):
        reg = SchemaRegistry()
        reg.register("divination.cast", SCHEMA)
        return reg

    def _env(self, payload):
        return envelope.build("divination.cast", payload, source="t")

    def test_valid_passes(self):
        reg = self._registry()
        reg.validate(self._env({"method": "tarot", "count": 3,
                                "tags": ["a"], "nested": {"x": 1.5}}))

    def test_missing_required(self):
        reg = self._registry()
        with pytest.raises(errors.ValidationError) as exc_info:
            reg.validate(self._env({}))
        assert any("method" in r for r in exc_info.value.details["reasons"])

    def test_wrong_type_and_enum_and_range(self):
        reg = self._registry()
        with pytest.raises(errors.ValidationError) as exc_info:
            reg.validate(self._env({"method": "tea", "count": 99,
                                    "name": "", "tags": [1],
                                    "nested": {"x": "nope"}}))
        reasons = exc_info.value.details["reasons"]
        assert len(reasons) >= 4  # all problems reported, not just the first

    def test_unregistered_type_open_by_default(self):
        reg = self._registry()
        reg.validate(envelope.build("other.type", {"anything": 1}, source="t"))

    def test_closed_mode_rejects_unknown(self):
        reg = SchemaRegistry(closed=True)
        with pytest.raises(errors.ValidationError):
            reg.validate(envelope.build("other.type", {}, source="t"))

    def test_require_per_type(self):
        reg = self._registry()
        reg.require("strict.type")
        with pytest.raises(errors.ValidationError):
            reg.validate(envelope.build("strict.type", {}, source="t"))

    def test_reasons_list_empty_when_valid(self):
        assert self._registry().reasons(self._env({"method": "runes"})) == []

    def test_register_replaces_schema(self):
        reg = self._registry()
        reg.register("divination.cast", {"type": "object", "required": []})
        reg.validate(self._env({}))  # now passes

    def test_pattern_constraint(self):
        reg = SchemaRegistry()
        reg.register("net.ping", {"type": "object", "properties": {
            "host": {"type": "string", "pattern": r"^\d+\.\d+\.\d+\.\d+$"}}})
        reg.validate(envelope.build("net.ping", {"host": "127.0.0.1"}, source="t"))
        with pytest.raises(errors.ValidationError):
            reg.validate(envelope.build("net.ping", {"host": "nope"}, source="t"))


# ---------------------------------------------------------------------------
# Slice 8: dispatcher + gateway
# ---------------------------------------------------------------------------

def _gateway():
    cfg = load_config(path="/nonexistent/path.yaml",
                      overrides={"heimdall": {"auth": {"api_keys": ["gw:test-key"]}}})
    gw = Gateway(config=cfg)
    gw.schemas.register("math.add", {
        "type": "object", "required": ["a", "b"],
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
    })
    gw.dispatcher.register("math.add",
                           lambda env: {"sum": env["payload"]["a"] + env["payload"]["b"]})
    return gw, "test-key"


class TestDispatcher:
    def test_dispatch_routes(self):
        disp = Dispatcher()
        disp.register("a.b", lambda env: {"echo": env["payload"]})
        assert disp.dispatch(envelope.build("a.b", {"x": 1}, source="t")) == {"echo": {"x": 1}}
        assert disp.routes() == ["a.b"]

    def test_unknown_type_dead_lettered(self):
        disp = Dispatcher(dead_letter_max=10)
        env = envelope.build("no.handler", {}, source="t")
        with pytest.raises(errors.NotFoundError):
            disp.dispatch(env)
        dl = disp.dead_letter()
        assert len(dl) == 1
        assert dl[0]["id"] == env["id"]
        assert "no handler" in dl[0]["reason"]

    def test_handler_failure_dead_lettered(self):
        disp = Dispatcher()
        def boom(env):
            raise RuntimeError("kaboom")
        disp.register("boom.x", boom)
        with pytest.raises(errors.DispatchError):
            disp.dispatch(envelope.build("boom.x", {}, source="t"))
        assert len(disp.dead_letter()) == 1

    def test_dead_letter_bounded_and_purge(self):
        disp = Dispatcher(dead_letter_max=3)
        for _ in range(5):
            try:
                disp.dispatch(envelope.build("nope.n", {}, source="t"))
            except errors.NotFoundError:
                pass
        assert len(disp.dead_letter()) == 3
        assert disp.purge_dead_letter() == 3
        assert disp.dead_letter() == []

    def test_unregister(self):
        disp = Dispatcher()
        disp.register("a.b", lambda env: {})
        assert disp.unregister("a.b") is True
        assert disp.unregister("a.b") is False

    def test_health(self):
        disp = Dispatcher()
        disp.register("a.b", lambda env: {"ok": True})
        disp.dispatch(envelope.build("a.b", {}, source="t"))
        h = disp.health()
        assert h["status"] == "ok"
        assert h["dispatched"] == 1
        assert h["handlers"] == ["a.b"]
        assert h["uptime_s"] >= 0


class TestGateway:
    def test_happy_path(self):
        gw, key = _gateway()
        raw = envelope.dumps(envelope.build("math.add", {"a": 2, "b": 3}, source="t"))
        resp = gw.handle(raw, key)
        assert resp["type"] == "math.add.response"
        assert resp["payload"] == {"ok": True, "result": {"sum": 5}}
        assert resp["meta"]["correlation_id"] == envelope.parse(raw)["id"]
        assert resp["source"] == "heimdall"

    def test_bad_key_rejected(self):
        gw, _ = _gateway()
        raw = envelope.dumps(envelope.build("math.add", {"a": 1, "b": 1}, source="t"))
        resp = gw.handle(raw, "wrong")
        assert resp["type"] == "heimdall.error"
        assert resp["payload"]["ok"] is False
        assert resp["payload"]["error"]["code"] == "auth_failed"
        assert resp["meta"]["http_status"] == 401

    def test_schema_violation_rejected(self):
        gw, key = _gateway()
        raw = envelope.dumps(envelope.build("math.add", {"a": "x"}, source="t"))
        resp = gw.handle(raw, key)
        assert resp["payload"]["error"]["code"] == "validation_failed"
        assert resp["payload"]["error"]["details"]["reasons"]

    def test_unknown_type_rejected_and_dead_lettered(self):
        gw, key = _gateway()
        raw = envelope.dumps(envelope.build("nope.unknown", {}, source="t"))
        resp = gw.handle(raw, key)
        assert resp["payload"]["error"]["code"] == "not_found"
        assert gw.dispatcher.dead_letter()[0]["type"] == "nope.unknown"

    def test_bad_version_rejected(self):
        gw, key = _gateway()
        env = envelope.build("math.add", {"a": 1, "b": 2}, source="t")
        env["v"] = 42
        resp = gw.handle(env, key)  # dict input also accepted
        assert resp["payload"]["error"]["code"] == "protocol_error"

    def test_malformed_input_rejected(self):
        gw, key = _gateway()
        resp = gw.handle("{bad json", key)
        assert resp["payload"]["error"]["code"] == "validation_failed"

    def test_fail_closed_without_keys(self):
        cfg = load_config(path="/nonexistent/path.yaml",
                          overrides={"heimdall": {"auth": {"api_keys": []}}})
        gw = Gateway(config=cfg)
        raw = envelope.dumps(envelope.build("math.add", {"a": 1, "b": 1}, source="t"))
        resp = gw.handle(raw, "any")
        assert resp["payload"]["error"]["code"] == "auth_failed"

    def test_health_report(self):
        gw, key = _gateway()
        raw = envelope.dumps(envelope.build("math.add", {"a": 1, "b": 2}, source="t"))
        gw.handle(raw, key)
        gw.handle(raw, "bad")
        h = gw.health_report()
        assert h["status"] == "ok"
        assert h["service"] == "heimdall"
        assert h["protocol_version"] == 1
        assert h["auth"]["keys_configured"] == 1
        assert h["gateway"]["accepted"] == 1
        assert h["gateway"]["rejected"] == 1
        assert h["dispatcher"]["dispatched"] == 1
        assert "math.add" in h["schemas"]

    def test_never_raises(self):
        gw, key = _gateway()
        for raw in [None, "", b"\x00", {"v": "x"}, []]:
            resp = gw.handle(raw, key)  # type: ignore[arg-type]
            assert resp["type"] == "heimdall.error"
            assert resp["payload"]["ok"] is False

    def test_handler_exception_becomes_error_envelope(self):
        gw, key = _gateway()
        def boom(env):
            raise RuntimeError("kaboom")
        gw.dispatcher.register("boom.x", boom)
        raw = envelope.dumps(envelope.build("boom.x", {}, source="t"))
        resp = gw.handle(raw, key)
        assert resp["payload"]["error"]["code"] == "dispatch_failed"
