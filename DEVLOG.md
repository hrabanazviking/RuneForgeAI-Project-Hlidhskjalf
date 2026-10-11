# DEVLOG — RuneForgeAI-Project-Hlidhskjalf

## 2026-10-11 — dawn forge run ("Hold the edges the dusk left unguarded", 20 slices) — @ 38da946

20-slice validation/robustness campaign over the modules the dusk run did not
touch. All slices derived from audits whose defects were reproduced before
fixing. Test suite: 530 passed / 1 skipped baseline → 657 passed / 1 skipped /
0 failed (658 collected; 127 new dawn tests). All pushes verified against origin.

**Track A — wyrd** (`2c761ee`): `add_edge` rejects bool/non-numeric/non-finite
weights with `ValidationError` (bool slipped through as 1.0, str raised a raw
`TypeError`); `add_node` validates node_id/node_type/attrs types; `from_dict`
rejects corrupt payloads, duplicate ids, and dangling edges instead of silently
keeping them; `causes_of`/`effects_of` share walk validation (`effects_of`
accepted decay 0/NaN silently, bool max_depth passed both); the query API
rejects unknown neighbor directions and no longer `int()`-coerces `max_depth`.

**Track B — protocol / common / heimdall** (`9f80f49`): envelope version must
be a real int (`v=True` and `v=1.0` passed the supported-set test); `validate`
and `build` reject NaN/inf timestamps; `new_id(at=...)` rejects non-finite /
out-of-48-bit-range timestamps (negative `at` produced ids that decoded to a
wrong huge time); `Authenticator.authenticate` raises `AuthError` for non-str
credentials instead of `AttributeError`, and `extract_bearer` tolerates
non-str header values; schema validation rejects non-finite numbers and
reports invalid regex patterns as problems instead of crashing on `re.error`.

**Track C — himinbjorg** (`315fd20`): `GridLayout` validates int coords/spans
(span 0 produced negative-width tiles) and finite positive canvas dims
(negative/NaN dims produced negative/NaN rects); `Theme` validates `#rrggbb`
colours and positive `font_size`, and `get_theme` raises `KeyError` for
non-string names per its contract; the celestial viewport skips bool/non-finite
planet degrees (NaN markers, `True` plotted as 1.0°); `InputRouter.focus_prev`
from no focus now lands on the last focusable instead of the second-to-last.

**Track D — draupnir / kista / verdandi / host** (`38da946`): `aggregate`
raises `ValueError` for non-mapping results and keeps both outputs when agent
ids collide (later output silently overwrote the earlier one); `MythicCoder`
pre-validates every source/path/python-syntax before writing, so a bad spec
leaves nothing on disk (partial forges were written, then failed);
`VersionGraph.history` validates its limit — `limit=0` returns `[]` (it
returned one record); `BranchManager.append` validates event_type/entity/ts/
payload (empty types and NaN timestamps were stored and corrupted merge
order); `top_processes` validates `top_n`/`sample_interval` (`top_n=-1`
returned nearly all processes via `[:-1]` slicing).

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
