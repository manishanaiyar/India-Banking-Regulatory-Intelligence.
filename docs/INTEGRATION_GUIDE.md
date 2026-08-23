# Integration Guide — v3 (bugs found + fixed via testing against your real files)

You shared `dpdp_ingest.py`, `dpdp_stores.py`, and `groq_client.py` after
v2. Reading them closely and testing against them **found and fixed two
real bugs** in v2 that would have surfaced in production. Both are now
fixed and verified with passing tests (real `dpdp_ingest.py` parsing
logic, real `dpdp_stores.py` `commit_section()`, a real generated PDF run
through real `pypdf` extraction, real simulated Neo4j Cypher calls).

## Bug 1 (critical, would have crashed): `entities` shape mismatch

Your real `dpdp_stores.KnowledgeStore.commit_section()` does:
```python
entities = section["entities"]
for label, items in (("Obligation", entities["obligations"]),
                      ("Right", entities["rights"]),
                      ("Penalty", entities["penalties"]),
                      ("Definition", entities["definitions"])):
```
This requires `entities` to be a **dict with exactly those four keys**,
matching your `dpdp_ingest.tag_entities()` output shape. v2's
`ingest_common.py` shipped `"entities": []` (an empty **list**) for the
new laws. The first time a KYC/PMLA/Cyber section got auto-approved and
committed, this would have raised:
```
TypeError: list indices must be integers or slices, not str
```
**Fixed** in `ingest_common.py`'s new `_build_entities()` helper, which
always returns the full 4-key dict shape. Verified with a test that
deliberately reproduces the old bug and confirms it does raise
`TypeError` — proving the bug was real, not theoretical — then confirms
the fixed version does not.

## Bug 2 (silent, compliance-relevant): singular/plural category mismatch

`dpdp_config.SENSITIVE_CATEGORIES = ("obligations", "penalties")` and
your `banking_config.py`'s per-law `sensitive_categories` tuples both use
**plural** category names. v2's `ingest_common.py` internally tagged
sections with **singular** names (`"obligation"`, `"penalty"`). The
membership check `category in sensitive_categories` would therefore
never match — `"penalty" != "penalties"` — meaning **penalty clauses in
the new laws would have silently skipped human review and auto-approved**,
exactly the outcome Checkpoint 1 exists to prevent.

**Fixed**: `CATEGORY_KEYWORDS` in `ingest_common.py` now uses the same
plural vocabulary throughout. Verified with a test tagging real
penalty-clause text and confirming it now correctly matches
`kyc_aml`'s `sensitive_categories` and gets flagged.

## Also changed: lazy store creation (operational risk, not a functional bug)

`dpdp_stores.KnowledgeStore.__init__()` opens a real Neo4j driver
connection (`GraphDatabase.driver(...)`). v2 eagerly created three extra
`KnowledgeStore()` instances (kyc_aml/pmla/rbi_cyber) at module import
time — meaning every app startup would open 3 additional Neo4j
connections to your AuraDB **Free** tier before anyone had used those
laws at all. AuraDB Free typically caps concurrent connections tightly.

`main.py` now creates each new law's `KnowledgeStore`/`ReviewQueue` lazily,
only on first use (first `/ingest/{law}` call, or first request touching
that law). A deploy that only ever serves DPDP traffic now opens exactly
the one Neo4j connection your original app already used — zero change in
behavior for the DPDP-only case.

## One real (unrelated) observation from testing, not a bug

While generating a test PDF and running your real `tag_entities()`
against it, testing confirmed: any section in **Chapter II - OBLIGATIONS
OF DATA FIDUCIARY** (sections 4-10) gets flagged `sensitive=True` purely
because the *chapter name* contains "OBLIGATIONS" — not because of that
specific section's own content. That's chapter-level detection by design
in your original code, not something this package touches or changes.
Flagging it here only as an FYI in case it's ever surprising that, e.g.,
Section 6 (Consent) needs human review even though "consent" itself isn't
a penalty/obligation-sounding word — it's the chapter title doing that.

## Everything else from the v2 guide still applies

- `requirements.txt`: add `pypdf` (confirmed your `dpdp_ingest.py` already
  uses it too, via `extract_text()` — so this isn't a new dependency
  choice, just confirming it needs to be in `requirements.txt` if it
  isn't already).
- RBI's site returned 403 Forbidden in testing — see the fetch-header
  note from the previous guide, still applies, not something this pass
  could resolve without a working fetch to iterate against.
- `main.py` is still a full drop-in replacement; DPDP behavior is
  unchanged for any client not sending the new optional `law` field.
- Frontend snippets are unchanged (still additive, not tested against
  your real frontend since it wasn't shared — noted in the file headers).

## Test coverage summary (all passing)

1. Real `dpdp_ingest.split_into_sections()` / `tag_entities()` /
   `estimate_confidence()` / `is_sensitive()` against synthetic Act text.
2. Real `KnowledgeStore.commit_section()` with a real DPDP-shaped section.
3. Real `commit_section()` with a new-law section (fixed shape) — no crash.
4. Same call with the **old buggy shape** — confirmed it DOES crash
   (proves bug 1 was real).
5. Category tagging + `sensitive_categories` matching for a penalty clause
   (proves bug 2 was real and is now fixed).
6. Full pipeline: a real generated PDF → real `pypdf` extraction → real
   parsing → real tagging → real review queue → real (stubbed-transport)
   Neo4j calls → `/ask` → clean handling of the (expected, no-network)
   Groq failure.

`neo4j` and `tfidf_search` are still stubs (not your real files — you
haven't shared them). Everything else in this test run was your actual
code.
