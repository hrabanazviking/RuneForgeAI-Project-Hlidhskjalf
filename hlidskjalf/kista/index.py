"""Slice 13 — Kista tag + full-text-ish artifact index.

A simple inverted index: tags map 1:1; text is tokenized (lowercased
alphanumeric tokens) from an optional ``text`` field passed at add time,
or from decoded artifact bytes when they are UTF-8.

``query(tags=..., text=...)`` ANDs tag filters with text tokens and
returns paginated results. The index persists as JSON beside the store.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from hlidskjalf.kista._compat import NotFoundError, get_logger, new_id
from hlidskjalf.kista.store import ArtifactStore

log = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> Set[str]:
    return set(_TOKEN.findall(text.lower()))


class ArtifactIndex:
    """Inverted index over artifact tags and text."""

    def __init__(self, store: ArtifactStore, path: Optional[str | os.PathLike[str]] = None) -> None:
        self.store = store
        self.path = Path(path) if path else Path(store.root) / "index.json"
        self._tags: Dict[str, Set[str]] = {}
        self._terms: Dict[str, Set[str]] = {}
        self._docs: Dict[str, Dict] = {}
        self.load()

    # -- persistence ---------------------------------------------------
    def save(self) -> None:
        data = {
            "tags": {k: sorted(v) for k, v in self._tags.items()},
            "terms": {k: sorted(v) for k, v in self._terms.items()},
            "docs": self._docs,
        }
        tmp = self.path.parent / f".tmp-{new_id()}.json"
        try:
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            tags = {k: set(v) for k, v in data.get("tags", {}).items()}
            terms = {k: set(v) for k, v in data.get("terms", {}).items()}
            docs = data.get("docs", {})
            if not isinstance(docs, dict):
                raise ValueError("index docs must be an object")
        except (json.JSONDecodeError, UnicodeDecodeError, OSError,
                ValueError, AttributeError, TypeError) as exc:
            # Corrupt index: quarantine it and rebuild from the store so the
            # index self-heals instead of killing the constructor.
            stamp = int(time.time())
            quarantine = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            try:
                self.path.rename(quarantine)
            except OSError:
                log.warning("kista.index.quarantine_failed path=%s", self.path)
                quarantine = None
            log.warning("kista.index.corrupt quarantined=%s error=%s", quarantine, exc)
            self.rebuild()
            return
        self._tags, self._terms, self._docs = tags, terms, docs

    # -- mutation ------------------------------------------------------
    def add(
        self,
        digest: str,
        tags: Optional[Iterable[str]] = None,
        text: Optional[str] = None,
    ) -> None:
        """Index an artifact. Falls back to decoding the artifact bytes."""
        tags = sorted(set(tags or []))
        if not self.store.exists(digest):
            raise NotFoundError(f"cannot index unknown digest: {digest}")
        if text is None:
            try:
                text = self.store.get(digest).decode("utf-8", errors="strict")
            except Exception:  # noqa: BLE001 - binary artifacts have no text
                text = ""
        terms = sorted(tokenize(text or ""))
        self.remove(digest)  # re-index cleanly
        self._docs[digest] = {"tags": tags, "terms": terms}
        for tag in tags:
            self._tags.setdefault(tag, set()).add(digest)
        for term in terms:
            self._terms.setdefault(term, set()).add(digest)

    def remove(self, digest: str) -> None:
        doc = self._docs.pop(digest, None)
        if doc is None:
            return
        for tag in doc["tags"]:
            bucket = self._tags.get(tag)
            if bucket:
                bucket.discard(digest)
                if not bucket:
                    del self._tags[tag]
        for term in doc["terms"]:
            bucket = self._terms.get(term)
            if bucket:
                bucket.discard(digest)
                if not bucket:
                    del self._terms[term]

    def rebuild(self) -> int:
        """Re-index every artifact in the store from sidecar metadata."""
        self._tags.clear()
        self._terms.clear()
        self._docs.clear()
        count = 0
        for digest in self.store.list_hashes():
            try:
                meta = self.store.get_meta(digest)
                self.add(digest, tags=meta.get("tags", []))
                count += 1
            except Exception:  # noqa: BLE001 - skip unreadable artifacts
                continue
        self.save()
        return count

    # -- query ---------------------------------------------------------
    def query(
        self,
        tags: Optional[Iterable[str]] = None,
        text: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> Dict:
        """AND-combined tag + text search, paginated."""
        tags = list(tags or [])
        candidate: Optional[Set[str]] = None

        for tag in tags:
            hits = self._tags.get(tag, set())
            candidate = hits if candidate is None else candidate & hits

        if text:
            for term in tokenize(text):
                hits = self._terms.get(term, set())
                candidate = hits if candidate is None else candidate & hits
            if candidate is None:
                candidate = set()

        results = sorted(candidate) if candidate is not None else sorted(self._docs)
        total = len(results)
        page = max(1, page)
        per_page = max(1, min(per_page, 100))
        start = (page - 1) * per_page
        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "results": results[start : start + per_page],
        }

    def all_tags(self) -> List[str]:
        return sorted(self._tags)

    def __len__(self) -> int:
        return len(self._docs)
