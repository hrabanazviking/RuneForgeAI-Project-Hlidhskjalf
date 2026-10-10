"""Dusk run A tests: bus wedge/backpressure, envelope caps, config/errors
hardening, and Heimdall dead-letter/auth hardening."""

import copy
import json
import queue
import threading
import time

import pytest

from hlidskjalf.common import config as config_mod
from hlidskjalf.common import errors
from hlidskjalf.common.config import ConfigError, load_yaml
from hlidskjalf.heimdall.auth import Authenticator
from hlidskjalf.heimdall.dispatch import Dispatcher
from hlidskjalf.protocol import Bus, envelope
from hlidskjalf.protocol.envelope import MAX_ENVELOPE_BYTES


# ---------------------------------------------------------------------------
# A1. Reply wedge: a duplicate/late reply on a full maxsize=1 pending queue
# must not block the dispatcher thread.
# ---------------------------------------------------------------------------

def test_a1_duplicate_reply_does_not_wedge_dispatcher():
    bus = Bus("dusk-a1")
    try:
        # Simulate the exact wedge state: requester consumed its reply but
        # request()'s finally has not popped _pending yet, and the maxsize=1
        # reply queue is already full.
        corr = "dusk-a1-corr"
        reply_q: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        reply_q.put({"seed": "already-consumed-reply"})
        with bus._lock:
            bus._pending[corr] = reply_q

        received = []
        bus.subscribe("ping.pong", lambda env: received.append(env))

        dup = envelope.build(
            "ping.pong.reply",
            {"late": True},
            meta={"is_reply": True, "correlation_id": corr},
        )
        # _deliver must return promptly even though the pending queue is full.
        worker = threading.Thread(target=bus._deliver, args=(dup,), daemon=True)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "dispatcher wedged on duplicate reply"

        # The duplicate was counted, and the bus still dispatches afterwards.
        assert bus.stats()["duplicate_replies_dropped"] == 1

        bus.publish("ping.pong", {"n": 1})
        deadline = time.time() + 5
        while not received and time.time() < deadline:
            time.sleep(0.01)
        assert len(received) == 1, "post-wedge message never reached subscriber"
        assert received[0]["payload"] == {"n": 1}
    finally:
        with bus._lock:
            bus._pending.pop("dusk-a1-corr", None)
        bus.stop()


# ---------------------------------------------------------------------------
# A2. Bounded inbox backpressure: drop-oldest, never block, stats surface it.
# ---------------------------------------------------------------------------

def test_a2_inbox_backpressure_drop_oldest():
    entered = threading.Event()
    release = threading.Event()

    def slow_handler(env):
        entered.set()
        release.wait(timeout=30)

    bus = Bus("dusk-a2", maxsize=3)
    try:
        bus.subscribe("test.slow", slow_handler)
        bus.subscribe("test.fast", lambda env: None)
        bus.publish("test.slow", {})
        assert entered.wait(timeout=5), "dispatcher never entered slow handler"

        # Dispatcher is stalled; flood the bounded inbox.
        for i in range(10):
            bus.publish("test.fast", {"seq": i})

        # Backlog never exceeds maxsize; the oldest 7 were shed.
        assert bus._inbox.qsize() <= 3
        stats = bus.stats()
        assert stats["dropped"] == 7, stats

        release.set()
        # Wait for the drain, then stop cleanly.
        deadline = time.time() + 5
        while bus.stats()["delivered"] < 1 + 3 and time.time() < deadline:
            time.sleep(0.01)
        assert bus.stats()["delivered"] >= 4
    finally:
        release.set()
        bus.stop()


def test_a2_config_honours_inbox_maxsize():
    class FakeConfig:
        def get(self, key, default=None):
            return 7 if key == "protocol.inbox_maxsize" else default

    bus = Bus("dusk-a2c", config=FakeConfig())
    try:
        assert bus._inbox.maxsize == 7
    finally:
        bus.stop()

    # Bad config values fall back to the constructor's maxsize, never wedge.
    class BadConfig:
        def get(self, key, default=None):
            return "not-an-int" if key == "protocol.inbox_maxsize" else default

    bus2 = Bus("dusk-a2c2", maxsize=42, config=BadConfig())
    try:
        assert bus2._inbox.maxsize == 42
    finally:
        bus2.stop()


# ---------------------------------------------------------------------------
# A3. Envelope size cap + request timeout validation.
# ---------------------------------------------------------------------------

def test_a3_envelope_build_rejects_oversize():
    assert MAX_ENVELOPE_BYTES == 16 * 1024 * 1024
    big_payload = {"blob": "x" * (MAX_ENVELOPE_BYTES + 1)}
    with pytest.raises(errors.ValidationError):
        envelope.build("test.big", big_payload)


def test_a3_envelope_parse_rejects_oversize():
    raw_env = {
        "v": 1,
        "id": "0" * 26,
        "ts": 1.0,
        "type": "test.big",
        "source": "",
        "payload": {"blob": "y" * (MAX_ENVELOPE_BYTES + 1)},
        "meta": {},
    }
    raw = json.dumps(raw_env)
    assert len(raw.encode("utf-8")) > MAX_ENVELOPE_BYTES
    with pytest.raises(errors.ValidationError):
        envelope.parse(raw)
    # Oversize dict input is also rejected.
    with pytest.raises(errors.ValidationError):
        envelope.parse(raw_env)
    # A normal envelope still parses.
    ok = envelope.build("test.ok", {"a": 1})
    assert envelope.parse(envelope.dumps(ok))["payload"] == {"a": 1}


def test_a3_request_rejects_negative_timeout():
    bus = Bus("dusk-a3")
    try:
        with pytest.raises(errors.ValidationError):
            bus.request("no.such", {}, timeout=-1)
        # Sanity: timeout=0 is still accepted (and raises TimeoutError when
        # nobody replies), proving we only reject negatives.
        with pytest.raises(errors.TimeoutError):
            bus.request("no.such", {}, timeout=0)
    finally:
        bus.stop()


# ---------------------------------------------------------------------------
# A4. Common hardening.
# ---------------------------------------------------------------------------

def test_a4_coerce_names_env_var_on_failure(monkeypatch):
    monkeypatch.setenv("HLIDSKJALF__HUD__FPS", "not-an-int")
    with pytest.raises(ConfigError) as excinfo:
        config_mod._env_overrides()
    assert "HLIDSKJALF__HUD__FPS" in str(excinfo.value)

    monkeypatch.setenv("HLIDSKJALF__LOGGING__LEVEL", "DEBUG")
    # float coercion path also names the var on failure; pick hud width? use
    # a float template — there is none in DEFAULTS, so exercise _coerce
    # directly for the float branch.
    with pytest.raises(ConfigError) as excinfo2:
        config_mod._coerce("abc", 1.5, "HLIDSKJALF__SOME__FLOAT")
    assert "HLIDSKJALF__SOME__FLOAT" in str(excinfo2.value)


def test_a4_error_from_dict_coerces_non_dict_details():
    err = errors.error_from_dict(
        {"error": {"code": "validation_failed", "message": "m", "details": "a string"}}
    )
    assert isinstance(err, errors.ValidationError)
    assert isinstance(err.details, dict)
    assert "a string" in err.details.values()
    # Round-trip of a proper dict still works.
    err2 = errors.error_from_dict(
        {"error": {"code": "not_found", "message": "m", "details": {"k": "v"}}}
    )
    assert isinstance(err2, errors.NotFoundError)
    assert err2.details == {"k": "v"}


def test_a4_validate_rejects_bool_for_int():
    cfg = copy.deepcopy(config_mod.DEFAULTS)
    cfg["heimdall"]["port"] = True
    with pytest.raises(ConfigError) as excinfo:
        config_mod.validate(cfg)
    assert any("heimdall.port" in p for p in excinfo.value.details["problems"])

    cfg2 = copy.deepcopy(config_mod.DEFAULTS)
    cfg2["hud"]["fps"] = False
    with pytest.raises(ConfigError):
        config_mod.validate(cfg2)

    # A genuine int still passes.
    cfg3 = copy.deepcopy(config_mod.DEFAULTS)
    config_mod.validate(cfg3)


def test_a4_yaml_comment_stripping_respects_quotes(tmp_path):
    path = tmp_path / "quoted.yaml"
    path.write_text(
        'greeting: "hello # not a comment"\n'
        "plain: value # real comment\n"
        "single: 'it # works too' # trailing\n"
        "# full line comment\n"
        "nested:\n"
        '  inner: "keep # this"\n',
        encoding="utf-8",
    )
    data = load_yaml(path)
    assert data["greeting"] == "hello # not a comment"
    assert data["plain"] == "value"
    assert data["single"] == "it # works too"
    assert data["nested"]["inner"] == "keep # this"


# ---------------------------------------------------------------------------
# A5. Heimdall hardening.
# ---------------------------------------------------------------------------

def test_a5_non_mapping_result_is_dead_lettered():
    disp = Dispatcher()
    disp.register("test.echo", lambda env: ["not", "a", "mapping"])
    env = envelope.build("test.echo", {})
    with pytest.raises(errors.DispatchError):
        disp.dispatch(env)
    dead = disp.dead_letter()
    assert any(item["type"] == "test.echo" for item in dead), dead
    assert disp.health()["dead_letter_count"] == 1


def test_a5_authenticator_rejects_non_string_entry():
    with pytest.raises(errors.ValidationError):
        Authenticator([123])
    # String entries still work as before.
    auth = Authenticator(["muse:muse-key", "plain-key"])
    assert auth.key_count == 2
    assert auth.authenticate("muse-key").ok
