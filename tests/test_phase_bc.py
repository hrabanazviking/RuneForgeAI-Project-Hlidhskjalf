"""Tests for SURGE Phase B (Kista vault, slices 9-14) and Phase C
(WYRD world model + Verdandi, slices 15-20) of Project Hlidskjalf."""

import json
import time

import pytest

from hlidskjalf.kista import (
    crypto,
    gc,
    index as kindex,
    store,
    sync as ksync,
    versions,
)
from hlidskjalf.kista._compat import NotFoundError, ValidationError
from hlidskjalf.verdandi import branches, timeline as vtimeline
from hlidskjalf.wyrd import api, graph, inference, snapshot


# ---------------------------------------------------------------------
# Slice 9 — content-addressed store
# ---------------------------------------------------------------------
class TestStore:
    def test_round_trip(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        h = s.put(b"hello wyrd", tags=["greet"], meta={"author": "yrsa"})
        assert s.get(h) == b"hello wyrd"
        meta = s.get_meta(h)
        assert meta["hash"] == h and meta["size"] == 10
        assert meta["tags"] == ["greet"] and meta["meta"]["author"] == "yrsa"

    def test_dedup_by_hash(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        h1 = s.put(b"same bytes")
        h2 = s.put(b"same bytes")
        assert h1 == h2
        assert s.stats()["artifacts"] == 1

    def test_sha256_addressing(self, tmp_path):
        import hashlib

        s = store.ArtifactStore(tmp_path / "kista")
        data = b"content addressed"
        assert s.put(data) == hashlib.sha256(data).hexdigest()

    def test_get_missing_raises(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        with pytest.raises(NotFoundError):
            s.get("0" * 64)

    def test_put_rejects_non_bytes(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        with pytest.raises(ValidationError):
            s.put("not bytes")  # type: ignore[arg-type]

    def test_list_hashes_and_delete(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        h = s.put(b"x")
        assert h in s.list_hashes()
        assert s.delete(h) is True
        assert not s.exists(h)
        assert s.delete(h) is False


# ---------------------------------------------------------------------
# Slice 10 — versioning & lineage
# ---------------------------------------------------------------------
class TestVersions:
    def test_history_walk(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        v1 = vg.create_version(b"genesis", message="v1")
        v2 = vg.create_version(b"second", parents=[v1], message="v2")
        v3 = vg.create_version(b"third", parents=[v2], message="v3")
        hist = vg.history(v3)
        assert [r["version_id"] for r in hist] == [v3, v2, v1]
        assert [r["message"] for r in hist] == ["v3", "v2", "v1"]

    def test_lineage_by_hash(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        v1 = vg.create_version(b"data")
        h = s.put(b"data")
        lin = vg.lineage(h)
        assert [r["version_id"] for r in lin] == [v1]

    def test_orphan_parent_rejected(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        with pytest.raises(NotFoundError):
            vg.create_version(b"x", parents=["nope"])

    def test_heads_and_children(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        v1 = vg.create_version(b"a")
        v2 = vg.create_version(b"b", parents=[v1])
        v3 = vg.create_version(b"c", parents=[v1])  # fork
        assert set(vg.heads()) == {v2, v3}
        assert set(vg.children(v1)) == {v2, v3}

    def test_get_data(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        v = vg.create_version(b"payload-bytes")
        assert vg.get_data(v) == b"payload-bytes"

    def test_validate_clean(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        vg.create_version(b"a")
        assert vg.validate() == []


# ---------------------------------------------------------------------
# Slice 11 — encryption at rest
# ---------------------------------------------------------------------
class TestCrypto:
    def test_backend_known(self):
        assert crypto.BACKEND in ("aesgcm", "mock")

    def test_seal_open_round_trip(self):
        km = crypto.KeyManager()
        km.generate("k1")
        enc = crypto.EncryptedStore(km, allow_insecure=True)
        blob = enc.seal(b"secret bytes")
        assert blob != b"secret bytes"
        assert enc.open(blob) == b"secret bytes"

    def test_key_rotation(self):
        km = crypto.KeyManager()
        enc = crypto.EncryptedStore(km, allow_insecure=True)
        old = enc.seal(b"before")
        km.rotate()
        new = enc.seal(b"after")
        assert crypto.envelope_key_id(old) != crypto.envelope_key_id(new)
        assert enc.open(old) == b"before"  # old key retained
        assert enc.open(new) == b"after"

    def test_keyring_save_load(self, tmp_path):
        km = crypto.KeyManager()
        km.generate("persist")
        enc = crypto.EncryptedStore(km, allow_insecure=True)
        blob = enc.seal(b"keep me")
        p = tmp_path / "keys.json"
        km.save(p)
        km2 = crypto.KeyManager.load(p)
        assert crypto.EncryptedStore(km2, allow_insecure=True).open(blob) == b"keep me"

    def test_unknown_key_rejected(self):
        km = crypto.KeyManager()
        with pytest.raises(NotFoundError):
            km.use("missing")

    def test_envelope_magic(self):
        km = crypto.KeyManager()
        enc = crypto.EncryptedStore(km, allow_insecure=True)
        blob = enc.seal(b"x")
        assert blob.startswith(crypto.ENVELOPE_MAGIC)


# ---------------------------------------------------------------------
# Slice 12 — GC
# ---------------------------------------------------------------------
class TestGC:
    def test_collect_removes_unreferenced(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        keep = s.put(b"referenced")
        drop = s.put(b"orphan")
        report = gc.collect(s, roots=[keep])
        assert report.kept == [keep]
        assert report.removed == [drop]
        assert report.reclaimed_bytes == 6
        assert s.exists(keep) and not s.exists(drop)

    def test_never_deletes_referenced(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        h = s.put(b"pinned")
        report = gc.collect(s, roots=[h])
        assert report.removed == []
        assert s.exists(h)

    def test_dry_run(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        h = s.put(b"orphan")
        report = gc.collect(s, roots=[], dry_run=True)
        assert report.removed == [h]
        assert s.exists(h)  # untouched

    def test_collect_from_versions(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        vg = versions.VersionGraph(s)
        vg.create_version(b"versioned")
        orphan = s.put(b"orphan")
        report = gc.collect_from_versions(s, vg)
        assert report.removed == [orphan]
        assert len(report.kept) == 1


# ---------------------------------------------------------------------
# Slice 13 — index
# ---------------------------------------------------------------------
class TestIndex:
    def test_tag_query(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        idx = kindex.ArtifactIndex(s)
        h1 = s.put(b"norse runes", tags=["magic", "norse"])
        h2 = s.put(b"fishing nets", tags=["norse"])
        idx.add(h1, tags=["magic", "norse"])
        idx.add(h2, tags=["norse"])
        r = idx.query(tags=["magic"])
        assert r["total"] == 1 and r["results"] == [h1]
        r = idx.query(tags=["norse"])
        assert r["total"] == 2

    def test_text_query(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        idx = kindex.ArtifactIndex(s)
        h = s.put("The völva chants galdr under moonlight".encode())
        idx.add(h)
        assert idx.query(text="galdr")["total"] == 1
        assert idx.query(text="sword")["total"] == 0

    def test_tag_and_text_and(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        idx = kindex.ArtifactIndex(s)
        h1 = s.put(b"galdr of fire", tags=["spell"])
        h2 = s.put(b"galdr of ice")
        idx.add(h1, tags=["spell"])
        idx.add(h2)
        r = idx.query(tags=["spell"], text="galdr")
        assert r["results"] == [h1]

    def test_pagination(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        idx = kindex.ArtifactIndex(s)
        for i in range(5):
            h = s.put(f"doc {i} lorem".encode(), tags=["t"])
            idx.add(h, tags=["t"], text=f"doc {i} lorem")
        p1 = idx.query(tags=["t"], page=1, per_page=2)
        p2 = idx.query(tags=["t"], page=2, per_page=2)
        p3 = idx.query(tags=["t"], page=3, per_page=2)
        assert p1["total"] == 5
        assert len(p1["results"]) == 2 and len(p2["results"]) == 2 and len(p3["results"]) == 1
        assert p1["results"] != p2["results"]

    def test_remove_and_persist(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        p = tmp_path / "idx.json"
        idx = kindex.ArtifactIndex(s, path=p)
        h = s.put(b"hello", tags=["x"])
        idx.add(h, tags=["x"])
        idx.save()
        idx2 = kindex.ArtifactIndex(s, path=p)
        assert idx2.query(tags=["x"])["total"] == 1
        idx2.remove(h)
        assert idx2.query(tags=["x"])["total"] == 0

    def test_rebuild(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "kista")
        idx = kindex.ArtifactIndex(s)
        h = s.put(b"rebuild me", tags=["r"])
        assert idx.rebuild() == 1
        assert idx.query(tags=["r"])["results"] == [h]


# ---------------------------------------------------------------------
# Slice 14 — sync
# ---------------------------------------------------------------------
class TestSync:
    def test_push_pull_round_trip(self, tmp_path):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        h = local.put(b"sync me", tags=["s"])
        report = vs.push()
        assert report.pushed == [h]
        assert vs.remote.get(h) == b"sync me"
        # pull a remote-only artifact back
        h2 = vs.remote.put(b"remote only")
        report = vs.pull()
        assert report.pulled == [h2]
        assert local.get(h2) == b"remote only"

    def test_sync_idempotent(self, tmp_path):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        local.put(b"a")
        vs.sync()
        report = vs.sync()
        assert report.pushed == [] and report.pulled == []

    def test_manifest_diff(self, tmp_path):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        h = local.put(b"only local")
        diff = vs.compare()
        assert diff["missing_in_remote"] == [h]
        assert diff["missing_in_local"] == []

    def test_metadata_conflict_newest_wins(self, tmp_path):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        h = local.put(b"same bytes", meta={"v": 1})
        vs.push()
        # simulate remote sidecar becoming newer
        rmeta = vs.remote.get_meta(h)
        rmeta["created_at"] = time.time() + 100
        rmeta["meta"] = {"v": 2}
        vs.remote._atomic_write(vs.remote._meta_path(h), json.dumps(rmeta).encode())
        report = vs.sync()
        assert report.metadata_conflicts == [h]
        assert report.conflict_resolutions[0]["winner"] == "remote"
        assert local.get_meta(h)["meta"] == {"v": 2}


# ---------------------------------------------------------------------
# Slice 15 — causal graph
# ---------------------------------------------------------------------
class TestGraph:
    def test_nodes_edges(self):
        g = graph.CausalGraph()
        a = g.add_node(node_type="omen", attrs={"sign": "raven"})
        b = g.add_node(node_type="event")
        e = g.add_edge(a, b, weight=0.8, label="foretells")
        assert g.get_node(a)["attrs"]["sign"] == "raven"
        assert e["weight"] == 0.8
        assert [n["id"] for n in g.successors(a)] == [b]
        assert [n["id"] for n in g.predecessors(b)] == [a]

    def test_find_nodes(self):
        g = graph.CausalGraph()
        g.add_node(node_type="omen", attrs={"sign": "raven"})
        g.add_node(node_type="omen", attrs={"sign": "wolf"})
        assert len(g.find_nodes(node_type="omen")) == 2
        assert len(g.find_nodes(attrs={"sign": "wolf"})) == 1

    def test_edge_validation(self):
        g = graph.CausalGraph()
        a = g.add_node()
        with pytest.raises(NotFoundError):
            g.add_edge(a, "missing")
        with pytest.raises(ValidationError):
            g.add_edge(a, a, weight=2.0)

    def test_remove_cascades_edges(self):
        g = graph.CausalGraph()
        a, b = g.add_node(), g.add_node()
        g.add_edge(a, b)
        g.remove_node(a)
        assert g.edge_count() == 0

    def test_persist_restore(self, tmp_path):
        g = graph.CausalGraph()
        a = g.add_node(node_type="x")
        b = g.add_node(node_type="y")
        g.add_edge(a, b, 0.5, "links")
        p = tmp_path / "g.json"
        g.save(str(p))
        g2 = graph.CausalGraph.load(str(p))
        assert g2.node_count() == 2 and g2.edge_count() == 1
        assert g2.get_node(a)["type"] == "x"


# ---------------------------------------------------------------------
# Slice 16 — inference
# ---------------------------------------------------------------------
class TestInference:
    def _chain(self):
        g = graph.CausalGraph()
        a = g.add_node(node_type="cause")
        b = g.add_node(node_type="mid")
        c = g.add_node(node_type="effect")
        g.add_edge(a, b, weight=0.9, label="leads")
        g.add_edge(b, c, weight=0.8, label="leads")
        return g, a, b, c

    def test_causes_ranked(self):
        g, a, b, c = self._chain()
        ranked = inference.causes_of(g, c)
        ids = [n["id"] for n, _ in ranked]
        assert ids == [b, a]  # nearer cause scores higher
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)
        assert ranked[0][1] == pytest.approx(0.8 * 0.7)
        assert ranked[1][1] == pytest.approx(0.8 * 0.7 * 0.9 * 0.7)

    def test_max_depth(self):
        g, a, b, c = self._chain()
        ranked = inference.causes_of(g, c, max_depth=1)
        assert [n["id"] for n, _ in ranked] == [b]

    def test_cycle_safe(self):
        g = graph.CausalGraph()
        a, b = g.add_node(), g.add_node()
        g.add_edge(a, b, 0.5)
        g.add_edge(b, a, 0.5)
        ranked = inference.causes_of(g, a, max_depth=6)
        assert {n["id"] for n, _ in ranked} == {a, b}

    def test_effects_of(self):
        g, a, b, c = self._chain()
        ranked = inference.effects_of(g, a)
        assert [n["id"] for n, _ in ranked] == [b, c]

    def test_unknown_node(self):
        g = graph.CausalGraph()
        with pytest.raises(NotFoundError):
            inference.causes_of(g, "nope")


# ---------------------------------------------------------------------
# Slice 17 — snapshots & diffs
# ---------------------------------------------------------------------
class TestSnapshot:
    def test_diff_detects_changes(self):
        g = graph.CausalGraph()
        a = g.add_node(node_type="keep")
        b = g.add_node(node_type="drop")
        snap_a = snapshot.snapshot(g)
        g.remove_node(b)
        c = g.add_node(node_type="new")
        g.add_edge(a, c, 0.4, "binds")
        d = snapshot.diff(snap_a, snapshot.snapshot(g))
        assert d["removed_nodes"] == [b]
        assert d["added_nodes"] == [c]
        assert d["changed_nodes"] == []
        assert len(d["added_edges"]) == 1
        assert d["added_edges"][0]["label"] == "binds"

    def test_diff_attr_change(self):
        g = graph.CausalGraph()
        a = g.add_node(node_type="x", attrs={"v": 1})
        snap_a = snapshot.snapshot(g)
        g.nodes[a]["attrs"]["v"] = 2
        d = snapshot.diff(snap_a, snapshot.snapshot(g))
        assert d["changed_nodes"] == [a]
        assert d["added_nodes"] == d["removed_nodes"] == []

    def test_diff_empty(self):
        g = graph.CausalGraph()
        s = snapshot.snapshot(g)
        assert snapshot.diff(s, snapshot.snapshot(g))["added_nodes"] == []


# ---------------------------------------------------------------------
# Slice 18 — timeline
# ---------------------------------------------------------------------
class TestTimeline:
    def test_append_and_order(self):
        tl = vtimeline.Timeline()
        e1 = tl.append("omen", "sky", ts=10.0)
        e2 = tl.append("battle", "field", ts=5.0)
        e3 = tl.append("feast", "hall", ts=7.0)
        assert [e["id"] for e in tl.all()] == [e2, e3, e1]
        assert tl.get(e1)["type"] == "omen"

    def test_range_query(self):
        tl = vtimeline.Timeline()
        tl.append("a", "e", ts=1.0)
        tl.append("b", "e", ts=5.0)
        tl.append("c", "e", ts=9.0)
        assert len(tl.range_query(2.0, 8.0)) == 1
        with pytest.raises(ValidationError):
            tl.range_query(8.0, 2.0)

    def test_before_after(self):
        tl = vtimeline.Timeline()
        e1 = tl.append("a", "e", ts=1.0)
        e2 = tl.append("b", "e", ts=2.0)
        e3 = tl.append("c", "e", ts=3.0)
        assert [e["id"] for e in tl.before(e3)] == [e2, e1]
        assert [e["id"] for e in tl.after(e1)] == [e2, e3]
        assert [e["id"] for e in tl.before(e3, limit=1)] == [e2]

    def test_by_entity_and_persist(self, tmp_path):
        tl = vtimeline.Timeline()
        tl.append("a", "thor", ts=1.0)
        tl.append("b", "odin", ts=2.0)
        assert len(tl.by_entity("thor")) == 1
        p = tmp_path / "tl.json"
        tl.save(str(p))
        tl2 = vtimeline.Timeline.load(str(p))
        assert len(tl2) == 2
        assert tl2.by_entity("odin")[0]["type"] == "b"


# ---------------------------------------------------------------------
# Slice 19 — branches
# ---------------------------------------------------------------------
class TestBranches:
    def _main_with(self, n=3):
        bm = branches.BranchManager()
        ids = [bm.main.append(f"t{i}", "hero", ts=float(i)) for i in range(n)]
        return bm, ids

    def test_branch_and_merge_clean(self):
        bm, ids = self._main_with()
        bm.branch("whatif", from_event=ids[1])
        bm.append("whatif", "vision", "seer", ts=10.0)
        result = bm.merge("whatif")
        assert result["conflicts"] == []
        assert len(result["merged"]) == 1
        assert len(bm.main) == 4
        assert "whatif" not in bm.list_branches()

    def test_merge_conflict_detection(self):
        bm, ids = self._main_with()
        bm.branch("alt", from_event=ids[0])
        # main diverges on "hero" after the branch point
        bm.main.append("battle", "hero", ts=99.0)
        # branch also touches "hero"
        bm.append("alt", "dream", "hero", ts=50.0)
        bm.append("alt", "omen", "raven", ts=51.0)  # no conflict
        result = bm.merge("alt")
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["entity"] == "hero"
        assert len(result["merged"]) == 1  # raven event merged
        assert len(bm.main.by_entity("raven")) == 1

    def test_branch_unknown(self):
        bm = branches.BranchManager()
        with pytest.raises(NotFoundError):
            bm.append("nope", "t", "e")
        with pytest.raises(NotFoundError):
            bm.merge("nope")

    def test_drop(self):
        bm, ids = self._main_with()
        bm.branch("tmp", from_event=ids[0])
        bm.drop("tmp")
        assert bm.list_branches() == []


# ---------------------------------------------------------------------
# Slice 20 — unified query API
# ---------------------------------------------------------------------
class TestAPI:
    def _world(self):
        g = graph.CausalGraph()
        a = g.add_node(node_type="omen", attrs={"sign": "raven"})
        b = g.add_node(node_type="event")
        g.add_edge(a, b, 0.9, "foretells")
        tl = vtimeline.Timeline()
        tl.append("omen-seen", "sky", ts=1.0)
        tl.append("war", "field", ts=2.0)
        return g, tl, a, b

    def test_node_query(self):
        g, tl, a, b = self._world()
        r = api.query(g, tl, {"kind": "node", "id": a})
        assert r["ok"] and r["node"]["attrs"]["sign"] == "raven"

    def test_nodes_search(self):
        g, tl, a, b = self._world()
        r = api.query(g, tl, {"kind": "nodes", "type": "omen"})
        assert r["ok"] and r["count"] == 1

    def test_neighbors(self):
        g, tl, a, b = self._world()
        r = api.query(g, tl, {"kind": "neighbors", "id": a})
        assert r["ok"] and [n["id"] for n in r["successors"]] == [b]

    def test_causes_effects(self):
        g, tl, a, b = self._world()
        r = api.query(g, tl, {"kind": "causes", "node": b})
        assert r["ok"] and r["causes"][0]["node"]["id"] == a
        r = api.query(g, tl, {"kind": "effects", "node": a})
        assert r["ok"] and r["effects"][0]["node"]["id"] == b

    def test_timeline_range(self):
        g, tl, a, b = self._world()
        r = api.query(g, tl, {"kind": "timeline", "t0": 0, "t1": 1.5})
        assert r["ok"] and r["count"] == 1

    def test_events_and_before(self):
        g, tl, a, b = self._world()
        r = api.query(g, tl, {"kind": "events", "entity": "field"})
        assert r["ok"] and r["count"] == 1
        eid = tl.by_entity("field")[0]["id"]
        r = api.query(g, tl, {"kind": "before", "event": eid})
        assert r["ok"] and r["count"] == 1

    def test_unknown_kind_and_bad_query(self):
        g, tl, a, b = self._world()
        assert api.query(g, tl, {"kind": "nope"})["ok"] is False
        assert api.query(g, tl, "garbage")["ok"] is False
        assert api.query(g, tl, {"kind": "node", "id": "missing"})["ok"] is False

    def test_results_jsonable(self):
        g, tl, a, b = self._world()
        for q in (
            {"kind": "node", "id": a},
            {"kind": "causes", "node": b},
            {"kind": "timeline", "t0": 0, "t1": 10},
        ):
            json.dumps(api.query(g, tl, q))  # must not raise
