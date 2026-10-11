"""Dawn forge 2026-10-11 — Track D: aggregate / coder / versions / branches / harvester."""

import pytest

from hlidskjalf.common.errors import ValidationError
from hlidskjalf.draupnir.aggregate import aggregate
from hlidskjalf.draupnir.coder import CodegenError, CodeSpec, MythicCoder
from hlidskjalf.host import harvester
from hlidskjalf.kista.store import ArtifactStore
from hlidskjalf.kista.versions import VersionGraph
from hlidskjalf.verdandi.branches import BranchManager
from hlidskjalf.verdandi.timeline import Timeline


# D1 — aggregate fan-in robustness
def test_d1_non_mapping_result_raises_valueerror():
    with pytest.raises(ValueError):
        aggregate(["oops"])
    with pytest.raises(ValueError):
        aggregate([{"agent_id": "a", "ok": True, "output": "x"}, 42])


def test_d1_duplicate_agent_ids_keep_both_outputs():
    report = aggregate([
        {"agent_id": "a", "ok": True, "output": "one"},
        {"agent_id": "a", "ok": True, "output": "two"},
    ])
    assert report["succeeded"] == 2
    assert sorted(report["outputs"].values()) == ["one", "two"]
    assert report["outputs"]["a"] == "one"


def test_d1_none_agent_id_gets_unique_key():
    report = aggregate([
        {"agent_id": None, "ok": True, "output": "one"},
        {"ok": True, "output": "two"},
    ])
    assert sorted(report["outputs"].values()) == ["one", "two"]


def test_d1_normal_report_unchanged():
    report = aggregate([
        {"agent_id": "a1", "ok": True, "output": "one"},
        {"agent_id": "a2", "ok": False, "error": "boom"},
    ])
    assert report["outputs"] == {"a1": "one"}
    assert report["merged_output"] == "one"


# D2 — coder pre-validation: no partial writes
def test_d2_syntax_error_writes_nothing(tmp_path):
    spec = CodeSpec(name="t", files={"good.py": "x = 1\n", "bad.py": "def broken(:\n"})
    with pytest.raises(CodegenError):
        MythicCoder().generate_code(spec, tmp_path)
    assert not (tmp_path / "good.py").exists()
    assert not (tmp_path / "bad.py").exists()


def test_d2_non_str_source_writes_nothing(tmp_path):
    spec = CodeSpec(name="t", files={"a.txt": "ok", "b.txt": 123})
    with pytest.raises(CodegenError):
        MythicCoder().generate_code(spec, tmp_path)
    assert not (tmp_path / "a.txt").exists()


def test_d2_escaping_path_writes_nothing(tmp_path):
    spec = CodeSpec(name="t", files={"a.txt": "ok", "../evil.txt": "no"})
    with pytest.raises(CodegenError):
        MythicCoder().generate_code(spec, tmp_path)
    assert not (tmp_path / "a.txt").exists()


def test_d2_valid_spec_still_writes(tmp_path):
    spec = CodeSpec(name="t", files={"mod.py": "x = 1\n", "note.txt": "hi"})
    written = MythicCoder().generate_code(spec, tmp_path)
    assert written == ["mod.py", "note.txt"]
    assert (tmp_path / "mod.py").read_text() == "x = 1\n"


# D3 — versions history limit
def _version_graph(tmp_path):
    return VersionGraph(ArtifactStore(str(tmp_path)))


def test_d3_history_limit_zero_is_empty(tmp_path):
    vg = _version_graph(tmp_path)
    v = vg.create_version(b"hi")
    assert vg.history(v, limit=0) == []


@pytest.mark.parametrize("bad", [-1, True, 1.5, "2"])
def test_d3_history_rejects_bad_limit(tmp_path, bad):
    vg = _version_graph(tmp_path)
    v = vg.create_version(b"hi")
    with pytest.raises(ValidationError):
        vg.history(v, limit=bad)


def test_d3_history_valid_limits_unchanged(tmp_path):
    vg = _version_graph(tmp_path)
    v1 = vg.create_version(b"one")
    v2 = vg.create_version(b"two", parents=[v1])
    assert len(vg.history(v2)) == 2
    assert len(vg.history(v2, limit=1)) == 1


# D4 — branch append validation
def _branch_manager():
    tl = Timeline()
    tl.append("seed", "ent", {}, ts=1.0)
    bm = BranchManager(tl)
    bm.branch("b1")
    return bm


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event_type": "", "entity": "e"},
        {"event_type": "t", "entity": ""},
        {"event_type": "t", "entity": "e", "ts": float("nan")},
        {"event_type": "t", "entity": "e", "ts": float("inf")},
        {"event_type": "t", "entity": "e", "payload": ["not", "a", "dict"]},
    ],
)
def test_d4_branch_append_rejects_bad(kwargs):
    bm = _branch_manager()
    with pytest.raises(ValidationError):
        bm.append("b1", **kwargs)


def test_d4_branch_append_and_merge_unchanged():
    bm = _branch_manager()
    bm.append("b1", "did", "hero", {"x": 1}, ts=2.0)
    result = bm.merge("b1")
    assert len(result["merged"]) == 1 and result["conflicts"] == []


# D5 — harvester top_processes guards
@pytest.mark.parametrize(
    "kwargs",
    [{"top_n": 0}, {"top_n": -1}, {"top_n": True}, {"top_n": 2.5},
     {"sample_interval": -0.1}, {"sample_interval": float("nan")}],
)
def test_d5_top_processes_rejects_bad_args(kwargs):
    with pytest.raises(ValueError):
        harvester.top_processes(**kwargs)


def test_d5_top_processes_valid():
    rows = harvester.top_processes(top_n=3, sample_interval=0.01)
    assert len(rows) <= 3
    assert all({"pid", "name", "cpu"} <= set(r) for r in rows)
