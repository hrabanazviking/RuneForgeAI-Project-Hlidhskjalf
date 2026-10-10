"""Forge Worker B — Hlidhskjalf dusk run regression tests (slices B1-B6).

Each slice: a real fix in hlidskjalf/kista plus a regression test here with
a concrete assertion proving the fix.
"""

import json
import os
import stat

import pytest

from hlidskjalf.kista import (
    crypto,
    index as kindex,
    store,
    sync as ksync,
    versions,
)
from hlidskjalf.kista._compat import NotFoundError, StorageError, ValidationError


# ---------------------------------------------------------------------
# B1 — path traversal: digests are validated before touching the FS
# ---------------------------------------------------------------------
class TestB1PathTraversal:
    def test_get_traversal_raises(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "vault")
        outside = tmp_path / "secret.txt"
        outside.write_text("outside-data")
        with pytest.raises(ValidationError):
            s.get("..secret.txt")
        assert outside.read_text() == "outside-data"  # untouched

    def test_delete_traversal_raises(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "vault")
        outside = tmp_path / "victim.txt"
        outside.write_text("victim-data")
        with pytest.raises(ValidationError):
            s.delete("..victim.txt")
        assert outside.read_text() == "victim-data"  # survives

    def test_traversal_variants_rejected(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "vault")
        for bad in ("../../etc/passwd", "", "not-a-digest", "f" * 63,
                    "g" * 64, "F" * 64, None, 12345):
            with pytest.raises(ValidationError):
                s.exists(bad)

    def test_round_trip_still_works(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "vault")
        d = s.put(b"hello", tags=["t"])
        assert s.get(d) == b"hello"
        assert s.delete(d) is True
        assert not s.exists(d)


# ---------------------------------------------------------------------
# B2 — VaultSync verifies destination integrity after copy
# ---------------------------------------------------------------------
class TestB2SyncIntegrity:
    def test_push_tampered_bytes_raises_and_cleans_up(self, tmp_path, monkeypatch):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        d = local.put(b"genuine bytes")
        monkeypatch.setattr(local, "get", lambda digest: b"tampered bytes")
        with pytest.raises(StorageError):
            vs.push()
        assert not vs.remote.exists(d)  # bad copy deleted, never replicated

    def test_push_counts_verified_copies(self, tmp_path):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        d = local.put(b"ok bytes")
        report = vs.push()
        assert report.verified_copies == 1
        assert report.failed_copies == 0
        assert vs.remote.get(d) == b"ok bytes"
        assert report.as_dict()["verified_copies"] == 1

    def test_pull_tampered_bytes_raises(self, tmp_path, monkeypatch):
        local = store.ArtifactStore(tmp_path / "local")
        vs = ksync.VaultSync(local, tmp_path / "remote")
        d = vs.remote.put(b"remote genuine")
        monkeypatch.setattr(vs.remote, "get", lambda digest: b"tampered bytes")
        with pytest.raises(StorageError):
            vs.pull()
        assert not local.exists(d)


# ---------------------------------------------------------------------
# B3 — corrupt JSON raises typed StorageError; index self-heals
# ---------------------------------------------------------------------
class TestB3CorruptJson:
    def test_corrupt_sidecar_raises_storage_error(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        d = s.put(b"data")
        s._meta_path(d).write_bytes(b"{not valid json")
        with pytest.raises(StorageError) as ei:
            s.get_meta(d)
        assert str(s._meta_path(d)) in str(ei.value)  # path in details

    def test_corrupt_version_record_raises_storage_error(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        vg = versions.VersionGraph(s)
        vid = vg.create_version(b"vdata")
        vg._path(vid).write_text("{broken json")
        with pytest.raises(StorageError) as ei:
            vg.get(vid)
        assert str(vg._path(vid)) in str(ei.value)

    def test_corrupt_index_self_heals(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        d = s.put(b"heal me please", tags=["heal"])
        idx = kindex.ArtifactIndex(s)
        idx.add(d, tags=["heal"])
        idx.save()
        idx.path.write_text("this is not json {{{")
        idx2 = kindex.ArtifactIndex(s)  # constructor must not die
        assert idx2.query(tags=["heal"])["total"] == 1  # reconstructed + usable
        quarantined = list(idx.path.parent.glob("index.json.corrupt-*"))
        assert len(quarantined) == 1  # corrupt file quarantined

    def test_corrupt_index_wrong_shape_self_heals(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        d = s.put(b"shape me", tags=["shape"])
        idx = kindex.ArtifactIndex(s)
        idx.save()
        idx.path.write_text(json.dumps([1, 2, 3]))  # valid JSON, wrong shape
        idx2 = kindex.ArtifactIndex(s)
        assert idx2.query(tags=["shape"])["total"] == 1


# ---------------------------------------------------------------------
# B4 — put() validates tags and meta up front
# ---------------------------------------------------------------------
class TestB4PutValidation:
    def test_non_string_tags_rejected(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        with pytest.raises(ValidationError):
            s.put(b"x", tags=[1, "a"])

    def test_unserializable_meta_rejected(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        with pytest.raises(ValidationError):
            s.put(b"x", meta={"x": object()})

    def test_valid_put_works(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        d = s.put(b"fine", tags=["a", "b"], meta={"k": "v"})
        meta = s.get_meta(d)
        assert meta["tags"] == ["a", "b"]
        assert meta["meta"] == {"k": "v"}
        assert s.get(d) == b"fine"


# ---------------------------------------------------------------------
# B5 — index.add rejects phantom digests
# ---------------------------------------------------------------------
class TestB5PhantomDigests:
    def test_add_unknown_digest_raises_not_found(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        idx = kindex.ArtifactIndex(s)
        with pytest.raises(NotFoundError):
            idx.add("f" * 64)  # never stored
        assert idx.query(tags=["anything"])["total"] == 0
        assert len(idx) == 0  # no phantom document indexed

    def test_add_known_digest_still_works(self, tmp_path):
        s = store.ArtifactStore(tmp_path / "v")
        idx = kindex.ArtifactIndex(s)
        d = s.put(b"real doc", tags=["real"])
        idx.add(d, tags=["real"])
        assert idx.query(tags=["real"])["total"] == 1


# ---------------------------------------------------------------------
# B6 — crypto hardening
# ---------------------------------------------------------------------
class TestB6Crypto:
    def test_truncated_envelope_rejected(self):
        key = os.urandom(32)
        with pytest.raises(ValidationError):
            crypto.decrypt(b"KST1", key)          # was IndexError
        with pytest.raises(ValidationError):
            crypto.decrypt(b"KST1\x02ab", key)    # declares kid, no nonce
        with pytest.raises(ValidationError):
            crypto.envelope_key_id(b"KST1")       # was IndexError
        with pytest.raises(ValidationError):
            crypto.envelope_key_id(b"")

    def test_keyring_permissions_0600(self, tmp_path):
        km = crypto.KeyManager()
        km.generate("k1")
        p = tmp_path / "keys.json"
        km.save(p)
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600

    def test_mock_backend_requires_opt_in(self, monkeypatch):
        monkeypatch.delenv(
            crypto.INSECURE_CRYPTO_ENV, raising=False
        )
        assert crypto.BACKEND == "mock"  # no `cryptography` in this env
        km = crypto.KeyManager()
        km.generate("k1")
        enc = crypto.EncryptedStore(km)  # allow_insecure=False default
        with pytest.raises(StorageError):
            enc.seal(b"secret")
        with pytest.raises(StorageError):
            crypto.encrypt(b"secret", km.get("k1"), "k1")

    def test_mock_backend_opt_in_param_works(self, monkeypatch):
        monkeypatch.delenv(crypto.INSECURE_CRYPTO_ENV, raising=False)
        km = crypto.KeyManager()
        km.generate("k1")
        enc = crypto.EncryptedStore(km, allow_insecure=True)
        blob = enc.seal(b"secret")
        assert enc.open(blob) == b"secret"

    def test_mock_backend_opt_in_env_works(self, monkeypatch):
        monkeypatch.setenv(crypto.INSECURE_CRYPTO_ENV, "1")
        km = crypto.KeyManager()
        km.generate("k1")
        enc = crypto.EncryptedStore(km)  # no param, env opts in
        assert enc.open(enc.seal(b"x")) == b"x"

    def test_health_surfaces_backend(self):
        h = crypto.health()
        assert h["backend"] == crypto.BACKEND == "mock"
        km = crypto.KeyManager()
        enc = crypto.EncryptedStore(km, allow_insecure=True)
        assert enc.health()["backend"] == "mock"
