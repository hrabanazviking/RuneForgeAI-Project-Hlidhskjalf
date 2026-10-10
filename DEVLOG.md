# DEVLOG — RuneForgeAI-Project-Hlidhskjalf

## 2026-10-10 — dusk forge run ("Weave the web tight", 20 slices) — @ 3299e68

20-slice resilience/error-correction/efficiency campaign. All slices derived from
read-only audits whose defects were reproduced before fixing; unverified
suspicions were not promoted. Test suite: 444 passed / 1 skipped baseline →
530 passed / 1 skipped / 0 failed. All pushes verified against origin.

**Track A — protocol / common / heimdall** (`959ceb1`): bus reply-wedge fix
(non-blocking `put_nowait`, `duplicate_replies_dropped` stat); bounded inbox
backpressure (maxsize, drop-oldest, `dropped` stat); `MAX_ENVELOPE_BYTES` (16 MiB)
in `parse`/`build`; `request(timeout<0)` → `ValidationError`; env-coercion
`ConfigError`s; non-dict details coerced in `error_from_dict`; bool rejected as
int; quote-aware YAML comment stripping; non-mapping dispatch results
dead-lettered; authenticator entry type-check.

**Track B — kista** (`0d1d50e`): path-traversal guard (digest must be 64 hex chars
+ resolve-and-contain); VaultSync re-hashes destination bytes with
`verified_copies`/`failed_copies`; corrupt JSON → `StorageError`, corrupt index
quarantines to `*.corrupt-<ts>` and rebuilds; `put` validates tags (str) and
JSON-serializable meta; `index.add` raises `NotFoundError` on unknown digests
(no phantom docs); crypto envelope bounds; keyring written 0o600 atomically;
mock crypto backend requires explicit opt-in (`allow_insecure=` / env) —
`tests/test_phase_bc.py` TestCrypto sites opt in explicitly.

**Track C — seidr / autonomy / verdandi** (`c931fa0`): `run_ensemble` rejects
non-finite outcomes; autonomy `tick` rejects NaN/inf `dt` (non-finite start /
interval guarded); timeline `append` keeps a parallel `_keys` list
(O(n²) → O(n log n), order-parity tested).

**Track D — draupnir / hailo / himinbjorg / host** (`3299e68`): embeddings thread
`dim` through bucket/sign; forge workers `put_nowait` to result queue with
dropped counter + `drain_results()`; exec prescan blocks bare
`eval/exec/compile/open/__import__`; compositor `tick` rejects NaN/inf; dice
`distribution` memoized (render ≈1× instead of ≈2× distribution cost); vitals
accepts `members`/`party`/list; divination normalizes tarot + rune cast shapes;
scheduler heapq (order parity proven, 800→3200 jobs: 16× → 4.5× growth); TTS
per-distinct-char frequency cache (72,765 sha256 → 2); MCP client timeout
plumbing (`TimeoutError` on hung server).
