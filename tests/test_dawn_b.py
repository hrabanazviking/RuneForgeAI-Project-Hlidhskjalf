"""Dawn forge 2026-10-11 — Track B: envelope / ids / auth / schema."""

import pytest

from hlidskjalf.common.errors import AuthError, ProtocolError, ValidationError
from hlidskjalf.common.ids import is_valid, new_id, timestamp_ms
from hlidskjalf.heimdall.auth import Authenticator
from hlidskjalf.heimdall.validate import SchemaRegistry
from hlidskjalf.protocol import envelope


# B1 — envelope version must be a real int
@pytest.mark.parametrize("bad_version", [True, False, 1.0, "1"])
def test_b1_validate_rejects_non_int_version(bad_version):
    env = envelope.build("a.b", {}, source="s")
    env["v"] = bad_version
    with pytest.raises(ProtocolError):
        envelope.validate(env)


def test_b1_validate_accepts_int_version():
    env = envelope.build("a.b", {}, source="s")
    assert envelope.validate(env)["v"] == 1


# B2 — envelope ts finiteness
@pytest.mark.parametrize("bad_ts", [float("nan"), float("inf"), float("-inf")])
def test_b2_validate_rejects_nonfinite_ts(bad_ts):
    env = envelope.build("a.b", {}, source="s")
    env["ts"] = bad_ts
    with pytest.raises(ValidationError):
        envelope.validate(env)


@pytest.mark.parametrize("bad_ts", [float("nan"), float("inf"), -1, True, "now"])
def test_b2_build_rejects_bad_ts(bad_ts):
    with pytest.raises(ValidationError):
        envelope.build("a.b", {}, source="s", ts=bad_ts)


def test_b2_build_accepts_valid_ts():
    env = envelope.build("a.b", {}, source="s", ts=1728460000)
    assert env["ts"] == 1728460000.0
    assert envelope.parse(envelope.dumps(env)) == env


# B3 — ids new_id(at=...)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -100.0, True, "now", 1e15])
def test_b3_new_id_rejects_bad_at(bad):
    with pytest.raises(ValueError):
        new_id(at=bad)


def test_b3_new_id_valid_at_round_trips():
    import time

    before = int(time.time() * 1000)
    nid = new_id(at=time.time())
    assert is_valid(nid)
    assert timestamp_ms(nid) >= before


# B4 — auth type safety
@pytest.mark.parametrize("bad", [123, b"secret", ["secret"], 4.5])
def test_b4_authenticate_non_str_credential(bad):
    auth = Authenticator(["alice:secret"])
    with pytest.raises(AuthError):
        auth.authenticate(bad)


def test_b4_authenticate_valid_still_works():
    auth = Authenticator(["alice:secret"])
    assert auth.authenticate("secret").principal == "alice"


def test_b4_extract_bearer_non_str_value():
    auth = Authenticator(["k"])
    assert auth.extract_bearer({"Authorization": 42}) is None
    assert auth.extract_bearer({"Authorization": "Bearer abc"}) == "abc"


# B5 — schema robustness
def _registry(schema):
    reg = SchemaRegistry()
    reg.register("t.x", schema)
    return reg


def test_b5_nan_number_fails():
    reg = _registry({"type": "object",
                     "properties": {"n": {"type": "number", "min": 0, "max": 10}}})
    assert reg.reasons({"type": "t.x", "payload": {"n": float("nan")}})
    assert reg.reasons({"type": "t.x", "payload": {"n": float("inf")}})


def test_b5_nonfinite_without_type_fails_min_max():
    reg = _registry({"type": "object", "properties": {"n": {"min": 0}}})
    assert reg.reasons({"type": "t.x", "payload": {"n": float("nan")}})


def test_b5_invalid_pattern_is_problem_not_crash():
    reg = _registry({"type": "object",
                     "properties": {"s": {"type": "string", "pattern": "["}}})
    problems = reg.reasons({"type": "t.x", "payload": {"s": "hi"}})
    assert problems and "pattern" in problems[0]


def test_b5_valid_payload_still_passes():
    reg = _registry({"type": "object",
                     "properties": {"n": {"type": "number", "min": 0},
                                    "s": {"type": "string", "pattern": "^a"}}})
    assert reg.reasons({"type": "t.x", "payload": {"n": 3, "s": "abc"}}) == []
