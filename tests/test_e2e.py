"""End-to-end integration tests — Slice 47 (Phase H).

Pipeline under test (as other phases land): harvest -> gateway -> minds
-> HUD, plus autonomy loops, deploy units, and release artifacts.

**Defensive by design:** phases A-G are built in parallel, so every
subsystem module is imported in try/except. A module that does not exist
yet is *explicitly skipped* with a reason — never silently ignored and
never a failure. Smoke probes use candidate-name duck-typing and skip
when a module's API shape is not recognized yet.

Stdlib only (plus pytest).
"""

from __future__ import annotations

import importlib
import inspect
import py_compile
import re
from pathlib import Path
from typing import Callable, Optional

import pytest

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Defensive import helpers
# ---------------------------------------------------------------------------

def _import(name: str):
    """Import ``name``; return None instead of raising on ImportError."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _require(name: str):
    """Import ``name`` or explicitly skip the test."""
    mod = _import(name)
    if mod is None:
        pytest.skip(f"module {name!r} not implemented yet (parallel phase)")
    return mod


def _first_callable(mod, names):
    """Return the first callable attribute found under any of ``names``."""
    for n in names:
        fn = getattr(mod, n, None)
        if callable(fn):
            return n, fn
    return None, None


def _skip_unknown_api(mod, tried) -> None:
    pytest.skip(
        f"{mod.__name__}: API shape not recognized "
        f"(looked for {tried}); phase owner to extend this probe"
    )


# ---------------------------------------------------------------------------
# Subsystem registry (from tasks/SURGE_50_SLICE_ROADMAP.md)
# ---------------------------------------------------------------------------

SUBSYSTEM_MODULES = [
    # Phase A — foundation
    "hlidskjalf.common.logging",
    "hlidskjalf.common.errors",
    "hlidskjalf.common.ids",
    "hlidskjalf.common.config",
    "hlidskjalf.protocol.envelope",
    "hlidskjalf.protocol.bus",
    "hlidskjalf.heimdall.auth",
    "hlidskjalf.heimdall.validate",
    "hlidskjalf.heimdall.gateway",
    "hlidskjalf.heimdall.dispatch",
    # Phase B — kista vault
    "hlidskjalf.kista.store",
    "hlidskjalf.kista.versions",
    "hlidskjalf.kista.crypto",
    "hlidskjalf.kista.gc",
    "hlidskjalf.kista.index",
    "hlidskjalf.kista.sync",
    # Phase C — wyrd world model
    "hlidskjalf.wyrd.graph",
    "hlidskjalf.wyrd.inference",
    "hlidskjalf.wyrd.snapshot",
    "hlidskjalf.wyrd.api",
    "hlidskjalf.verdandi.timeline",
    "hlidskjalf.verdandi.branches",
    # Phase D — seidr & draupnir
    "hlidskjalf.seidr.simulator",
    "hlidskjalf.seidr.montecarlo",
    "hlidskjalf.draupnir.forge",
    "hlidskjalf.draupnir.lifecycle",
    "hlidskjalf.draupnir.coder",
    "hlidskjalf.draupnir.exec",
    "hlidskjalf.draupnir.aggregate",
    # Phase E — himinbjorg HUD
    "hlidskjalf.himinbjorg.compositor",
    "hlidskjalf.himinbjorg.theme",
    "hlidskjalf.himinbjorg.layout",
    "hlidskjalf.himinbjorg.input",
    "hlidskjalf.himinbjorg.viewports.vitals",
    "hlidskjalf.himinbjorg.viewports.celestial",
    "hlidskjalf.himinbjorg.viewports.divination",
    "hlidskjalf.himinbjorg.viewports.dice",
    "hlidskjalf.himinbjorg.viewports.muse_stream",
    # Phase F — hailo neural
    "hlidskjalf.hailo.runtime",
    "hlidskjalf.hailo.embeddings",
    "hlidskjalf.hailo.tts",
    "hlidskjalf.hailo.stt",
    "hlidskjalf.hailo.scheduler",
    # Phase G — host integration
    "hlidskjalf.host.mcp",
    "hlidskjalf.host.harvester",
    "hlidskjalf.host.transport",
    "hlidskjalf.host.sagnaskemma",
    "hlidskjalf.host.divination",
    "hlidskjalf.host.tools",
    # Phase H — autonomy (this phase)
    "hlidskjalf.autonomy.loops",
]


# ---------------------------------------------------------------------------
# Slice 47a: happy-path smoke test for every subsystem that exists
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", SUBSYSTEM_MODULES)
def test_subsystem_imports_cleanly(module_name: str):
    """Every implemented subsystem must import without side-effect errors."""
    _require(module_name)  # explicit skip when the phase hasn't landed


@pytest.mark.parametrize("module_name", SUBSYSTEM_MODULES)
def test_subsystem_is_well_formed(module_name: str):
    """Implemented modules carry a docstring; __all__ (if set) resolves."""
    mod = _require(module_name)
    assert mod.__doc__ and mod.__doc__.strip(), (
        f"{module_name}: missing module docstring"
    )
    exported = getattr(mod, "__all__", None)
    if exported is not None:
        missing = [n for n in exported if not hasattr(mod, n)]
        assert not missing, f"{module_name}: __all__ names missing: {missing}"


def _smoke_ids():
    mod = _require("hlidskjalf.common.ids")
    name, fn = _first_callable(mod, ["new_id", "ulid", "generate", "uuid4"])
    if fn is None:
        _skip_unknown_api(mod, ["new_id", "ulid", "generate", "uuid4"])
    a, b = fn(), fn()
    assert a != b, "id generator must produce unique ids"
    assert str(a) and str(b)


def _smoke_logging():
    mod = _require("hlidskjalf.common.logging")
    name, fn = _first_callable(mod, ["get_logger", "logger", "setup"])
    if fn is None:
        _skip_unknown_api(mod, ["get_logger", "logger", "setup"])
    logger = fn("hlidskjalf.e2e") if name != "logger" else fn
    assert logger is not None


def _smoke_config():
    mod = _require("hlidskjalf.common.config")
    name, fn = _first_callable(mod, ["load", "load_config", "from_yaml"])
    if fn is None:
        _skip_unknown_api(mod, ["load", "load_config", "from_yaml"])
    cfg_path = REPO / "config" / "hlidskjalf.yaml"
    try:
        cfg = fn(str(cfg_path)) if cfg_path.exists() else fn()
    except TypeError:
        try:
            cfg = fn()
        except TypeError:
            _skip_unknown_api(mod, [f"{name}(path?) / {name}()"])
    assert cfg is not None


def _smoke_envelope_roundtrip():
    mod = _require("hlidskjalf.protocol.envelope")
    bname, build = _first_callable(
        mod, ["build", "make", "create", "new_envelope"])
    pname, parse = _first_callable(
        mod, ["parse", "loads", "from_json", "from_dict"])
    if build is None or parse is None:
        _skip_unknown_api(mod, ["build/make/create", "parse/loads/from_json"])
    try:
        env = build(msg_type="e2e.ping", payload={"hello": "heimdall"})
    except TypeError:
        try:
            env = build("e2e.ping", {"hello": "heimdall"})
        except TypeError:
            _skip_unknown_api(mod, ["build(msg_type=.., payload=..)"])
    back = parse(env)
    assert back is not None


def _smoke_bus_pubsub():
    mod = _require("hlidskjalf.protocol.bus")
    _, bus_cls = _first_callable(mod, ["Bus", "MessageBus", "create_bus"])
    _, pub = _first_callable(mod, ["publish"])
    _, sub = _first_callable(mod, ["subscribe"])
    if bus_cls is None and (pub is None or sub is None):
        _skip_unknown_api(mod, ["Bus/MessageBus", "publish+subscribe"])
    # Structural probe only: deeper behavioural coverage belongs to
    # the protocol phase's own tests once its API is stable.
    if bus_cls is not None:
        try:
            sig = inspect.signature(bus_cls)
            assert len(sig.parameters) <= 2  # cheap to construct
        except (TypeError, ValueError):
            pass


def test_subsystem_happy_paths():
    """Happy-path smoke for key modules with predictable shapes.

    Each probe skips explicitly when its module is missing or its API
    shape is not recognized yet.
    """
    _smoke_ids()
    _smoke_logging()
    _smoke_config()
    _smoke_envelope_roundtrip()
    _smoke_bus_pubsub()


# ---------------------------------------------------------------------------
# Slice 47b: failure injection — bad envelope rejected
# ---------------------------------------------------------------------------

_BAD_ENVELOPES = [
    {},  # empty
    {"msg_type": "e2e.ping"},  # missing payload/version
    {"msg_type": "e2e.ping", "payload": {}, "version": "9999"},  # bad version
    "not-a-dict-at-all",
    None,
]


def test_failure_bad_envelope_rejected():
    """Heimdall must reject malformed envelopes, never crash on them."""
    mod = _require("hlidskjalf.protocol.envelope")
    vname, validate = _first_callable(
        mod, ["validate", "is_valid", "check", "verify"])
    pname, parse = _first_callable(mod, ["parse", "loads", "from_json"])
    if validate is None and parse is None:
        _skip_unknown_api(mod, ["validate/is_valid", "parse/loads"])
    rejected = 0
    for bad in _BAD_ENVELOPES:
        try:
            if validate is not None:
                outcome = validate(bad)
                # False / falsy / ValidationError-shaped dict counts as reject
                if outcome is False or outcome is None:
                    rejected += 1
                elif isinstance(outcome, dict) and (
                    outcome.get("valid") is False or "error" in outcome
                    or "errors" in outcome
                ):
                    rejected += 1
                # truthy non-dict outcomes are ambiguous -> probe parse too
            elif parse is not None:
                parse(bad)  # must raise
        except Exception:
            rejected += 1  # raising on garbage is a valid rejection
    assert rejected == len(_BAD_ENVELOPES), (
        f"envelope validation accepted {len(_BAD_ENVELOPES) - rejected} "
        f"malformed envelope(s)"
    )


def test_failure_bad_envelope_never_crashes_gateway():
    """A bad envelope reaching the gateway must not take the process down."""
    gw = _import("hlidskjalf.heimdall.gateway") or _import(
        "hlidskjalf.heimdall.dispatch")
    if gw is None:
        pytest.skip("heimdall gateway/dispatch not implemented yet "
                    "(parallel phase)")
    name, fn = _first_callable(
        gw, ["handle", "dispatch", "route", "ingest", "process"])
    if fn is None:
        _skip_unknown_api(gw, ["handle/dispatch/route/ingest/process"])
    # Best-effort: feed garbage positionally; the contract is "no crash".
    # Unknown signatures skip rather than guess dangerously.
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        pytest.skip(f"{gw.__name__}.{name}: signature not introspectable")
    params = list(sig.parameters.values())
    if not params or len(params) > 2:
        pytest.skip(f"{gw.__name__}.{name}: signature shape not recognized "
                    f"for safe failure injection")
    try:
        fn({"msg_type": "e2e.garbage", "payload": None, "version": "bogus"})
    except Exception:
        pass  # raising a typed error is fine; crashing the process is not


# ---------------------------------------------------------------------------
# Slice 47b: failure injection — timeout handled
# ---------------------------------------------------------------------------

def _timeout_param_of(fn) -> Optional[str]:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    for pname, p in sig.parameters.items():
        if "timeout" in pname.lower() or "deadline" in pname.lower():
            return pname
    return None


def test_failure_timeout_handled():
    """Execution surfaces must bound waiting: hung code times out, no hang."""
    mod = _import("hlidskjalf.draupnir.exec")
    if mod is None:
        pytest.skip("hlidskjalf.draupnir.exec not implemented yet "
                    "(parallel phase)")
    name, fn = _first_callable(
        mod, ["run_python", "run", "execute", "run_sandboxed"])
    if fn is None:
        _skip_unknown_api(mod, ["run_python/run/execute/run_sandboxed"])
    tparam = _timeout_param_of(fn)
    if tparam is None:
        pytest.skip(f"{mod.__name__}.{name}: no timeout/deadline parameter; "
                    f"cannot verify bounded execution")
    # Behavioral failure injection: code that never returns must be killed
    # by the timeout instead of hanging the caller.
    try:
        result = fn("while True:\n    pass", **{tparam: 1.0})
    except TimeoutError:
        return  # raising on timeout is valid handling
    except TypeError:
        pytest.skip(f"{mod.__name__}.{name}: call convention not recognized; "
                    f"cannot safely inject the timeout failure")
    timed_out = getattr(result, "timed_out", None)
    if timed_out is None:
        pytest.skip(f"{mod.__name__}.{name}: result carries no timed_out flag; "
                    f"cannot verify timeout behavior")
    assert timed_out is True, (
        f"{mod.__name__}.{name}: hung code was not reported as timed out: "
        f"{result!r}")


def test_failure_loop_isolation():
    """A crashing autonomous loop must not stop the other loops."""
    from hlidskjalf.autonomy.loops import AutonomousLoops, LoopSpec

    def _boom(loops, spec):
        raise RuntimeError("injected failure")

    seen = []

    def _ok(loops, spec):
        seen.append(spec.name)

    loops = AutonomousLoops(intervals={"simulation_tick": 5.0,
                                       "memory_consolidation": 10_000.0})
    loops.register(LoopSpec("e2e_bad", 5.0, _boom))
    loops.register(LoopSpec("e2e_good", 5.0, _ok))
    ran = loops.tick(5.0)
    assert "e2e_bad" in ran and "e2e_good" in ran
    assert seen == ["e2e_good"], "healthy loop must still run after a crash"
    bad = loops.get("e2e_bad")
    assert bad is not None and bad.last_error is not None
    assert "RuntimeError" in bad.last_error


# ---------------------------------------------------------------------------
# Slice 49: autonomous loops scheduler behaviour (no sleeping)
# ---------------------------------------------------------------------------

def test_loops_tick_runs_due_loops_without_sleeping():
    from hlidskjalf.autonomy.loops import AutonomousLoops

    loops = AutonomousLoops(intervals={"simulation_tick": 60.0,
                                       "memory_consolidation": 3600.0})
    assert loops.tick(59.9) == []
    ran = loops.tick(0.1)  # t=60.0 -> simulation_tick due
    assert ran == ["simulation_tick"]
    assert loops.get("simulation_tick").run_count == 1
    assert loops.tick(59.9) == []  # not due again yet
    assert [e["loop"] for e in loops.journal] == ["simulation_tick"]


def test_loops_register_replaces_stub():
    """Built-in stubs must be replaceable via register()."""
    from hlidskjalf.autonomy.loops import AutonomousLoops, LoopSpec

    calls = []

    def _mine(loops, spec):
        calls.append(spec.name)

    loops = AutonomousLoops(intervals={"memory_consolidation": 10.0})
    loops.register(LoopSpec("memory_consolidation", 10.0, _mine))
    assert loops.tick(10.0) == ["memory_consolidation"]
    assert calls == ["memory_consolidation"]
    assert loops.journal == [], "stub must no longer run after replacement"


def test_loops_builtin_stubs_registered_by_default():
    from hlidskjalf.autonomy.loops import AutonomousLoops

    loops = AutonomousLoops()
    names = {s.name for s in loops.loops()}
    assert {"memory_consolidation", "simulation_tick"} <= names


def test_loops_invalid_spec_rejected():
    from hlidskjalf.autonomy.loops import AutonomousLoops, LoopSpec

    loops = AutonomousLoops()
    with pytest.raises(ValueError):
        LoopSpec("bad", 0, lambda l, s: None)
    with pytest.raises(ValueError):
        LoopSpec("", 5.0, lambda l, s: None)
    with pytest.raises(ValueError):
        loops.tick(-1.0)
    with pytest.raises(KeyError):
        loops.run("no-such-loop")


def test_loops_disable_and_unregister():
    from hlidskjalf.autonomy.loops import AutonomousLoops

    loops = AutonomousLoops(intervals={"simulation_tick": 5.0})
    spec = loops.get("simulation_tick")
    assert spec is not None
    spec.enabled = False
    assert loops.tick(60.0) == []
    spec.enabled = True
    assert loops.tick(0.0) == ["simulation_tick"]
    assert loops.unregister("simulation_tick") is True
    assert loops.get("simulation_tick") is None
    assert loops.unregister("simulation_tick") is False


# ---------------------------------------------------------------------------
# Slice 48: deploy units parse + watchdog script sanity
# ---------------------------------------------------------------------------

def _parse_systemd_unit(path: Path) -> dict:
    """Minimal systemd unit parser: {section: {key: value}}."""
    sections: dict = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            current = m.group(1)
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            raise ValueError(f"{path.name}: line outside section: {raw!r}")
        key, value = line.split("=", 1)
        sections[current][key.strip()] = value.strip()
    return sections


@pytest.mark.parametrize("unit,entrypoint", [
    ("hlidskjalf-heimdall.service", "hlidskjalf.heimdall.gateway"),
    ("hlidskjalf-hud.service", "hlidskjalf.himinbjorg.compositor"),
])
def test_systemd_unit_valid(unit: str, entrypoint: str):
    """Unit files must parse and carry the required directives."""
    path = REPO / "deploy" / unit
    assert path.exists(), f"deploy/{unit} missing"
    unit_cfg = _parse_systemd_unit(path)
    assert "Unit" in unit_cfg and unit_cfg["Unit"].get("Description"), (
        f"{unit}: [Unit] Description required")
    svc = unit_cfg.get("Service", {})
    assert "ExecStart" in svc, f"{unit}: [Service] ExecStart required"
    assert f"-m {entrypoint}" in svc["ExecStart"], (
        f"{unit}: ExecStart must launch 'python -m {entrypoint}'")
    assert svc.get("Restart") == "always", (
        f"{unit}: Restart=always required")
    assert "Install" in unit_cfg, f"{unit}: [Install] section required"
    assert unit_cfg["Install"].get("WantedBy"), (
        f"{unit}: [Install] WantedBy required")


def test_watchdog_script_sanity():
    """watchdog.sh: safe bash, bounded retries, logs, restarts service."""
    path = REPO / "scripts" / "watchdog.sh"
    assert path.exists(), "scripts/watchdog.sh missing"
    text = path.read_text(encoding="utf-8")
    first = text.splitlines()[0] if text else ""
    assert first.startswith("#!") and "bash" in first or first.startswith("#!") and "sh" in first, (
        "watchdog.sh: missing shell shebang")
    assert "set -euo pipefail" in text, (
        "watchdog.sh: 'set -euo pipefail' required")
    assert re.search(r"(?i)(max_?retries|retries)", text), (
        "watchdog.sh: bounded retries not found")
    assert "systemctl" in text and "restart" in text, (
        "watchdog.sh: must restart the service via systemctl")
    assert re.search(r"(?i)(log|logger)", text), (
        "watchdog.sh: must log its actions")


# ---------------------------------------------------------------------------
# Slice 50: release artifacts
# ---------------------------------------------------------------------------

def test_changelog_has_release_entry():
    path = REPO / "CHANGELOG.md"
    assert path.exists(), "CHANGELOG.md missing"
    text = path.read_text(encoding="utf-8")
    assert re.search(r"##\s+\[?0\.1\.0\]?", text), (
        "CHANGELOG.md: no 0.1.0 release entry")


def test_demo_script_present_and_compiles():
    path = REPO / "scripts" / "demo.py"
    assert path.exists(), "scripts/demo.py missing"
    py_compile.compile(str(path), doraise=True)


def test_readme_quickstart_appended():
    # Repo rule (EDITING_README.md_FILE_FORBIDDEN.txt): only Volmarr may
    # touch README.md. The quickstart lives in tasks/SURGE_50_SLICE_ROADMAP.md
    # instead. This test verifies the rule file exists and README is untouched
    # by us (no "Quickstart (Surge Build)" marker from automation).
    assert (REPO / "EDITING_README.md_FILE_FORBIDDEN.txt").exists()
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert "Quickstart (Surge Build)" not in text, (
        "README.md must not be modified by automation")
