# India Banking Regulatory Intelligence - GraphRAG Assistant with Human-in-the-Loop Review

A production deployment of a policy knowledge-graph and retrieval-augmented assistant covering
**six regulatory frameworks** (India + EU) across four compliance pillars - Privacy, RBI/KYC,
Cybersecurity, and Insurance - each with its own knowledge graph, retrieval index, and
human-review queue. Deployed on free-tier cloud infrastructure. No local setup required to use it.

**Live demo:** https://india-dpdp-act-graphrag-assistant-with-w6t0.onrender.com
**API:** https://india-dpdp-act-graphrag-assistant-with.onrender.com/docs

## What this is

This started as a single-law DPDP Act assistant and grew into a multi-law, multi-jurisdiction
regulatory intelligence platform. It combines: a **knowledge graph per law** connecting sections
to their obligations, rights, and penalties; a **lexical retrieval + reranking pipeline** (TF-IDF
with MMR diversity and exact-phrase reranking - see *RAG pipeline engineering notes* below) over
each law's text; a **hosted LLM** that retrieves and generates cited answers, with a post-generation
citation-faithfulness check as an anti-hallucination guard; a **Data Classification + Policy
Engine** that tags both ingested regulatory text and live queries against a customer-data
taxonomy and recommends (and can actually apply) masking/encryption/tokenisation; a
**human-in-the-loop review gate** so nothing unverified reaches an end user; and an **append-only
audit trail** covering queries, review decisions, and ingestion-time classifications. Packaged as
a FastAPI backend and a static HTML/JS frontend, deployed independently as two Render services.

## Architecture

```
   RBI / KYC          Privacy            Cybersecurity        Insurance
 KYC/AML, PMLA,   DPDP Act, 2023      RBI Cyber Security    IRDAI Protection
 CDD, Beneficial   GDPR (EU)           Framework + CERT-In   of Policyholders'
 Owner             2016/679            Incident Reporting    Interests, 2017
      |                |                     |                    |
      +--------+-------+---------+-----------+--------------------+
               |
               v
      DATA CLASSIFICATION  (policy_engine.classify_text)
Customer PII | Financial Data | Transaction Data | Sensitive Data
               |
               v
          POLICY ENGINE  (policy_engine.evaluate / crypto_utils.apply_controls)
  Masking       Encryption       Tokenisation
(regex, partial  (Fernet,         (surrogate token
 reveal)          reversible)      + reversible vault)
               |
               v
       AUDIT & MONITORING  (audit_log.py - SQLite, append-only, DB-enforced)
  query_log | review_decision_log | ingestion_policy_log
```

Per law, the ingestion pipeline is:

```
Official source document (PDF or HTML, fetched live)
        |
        v
Parse into numbered sections (regex for DPDP/GDPR/IRDAI/PMLA, LLM-based extraction for KYC/Cyber)
        |
        v
Rule-based/LLM tagging: category (Obligation/Right/Penalty/Definition) + confidence
        |
        v
Data Classification: policy_engine.evaluate() run against the section's own text
        |
   +----+----------------------------------------+
   |                                              |
   v                                              v
Auto-approved                              Held for HUMAN REVIEW
(safe category, high confidence,           (touches Obligation/Penalty, low confidence,
 no sensitive_data match)                   verbatim-check failure, or sensitive_data match)
   |                                              |
   v                                              v
Indexed for TF-IDF search                  Sits in a review queue until a human
+ written to Neo4j (graph, with            approves/rejects it via the sidebar -
  data_classes/required_controls)          only then is it indexed and written
   |                                        to the graph too
   v
FastAPI backend (/ask, /pending-review, /health, /stats, /classify, /evaluate, /protect)
   |
   v
On a question: TF-IDF search + MMR/phrase reranking retrieves the most relevant sections for
the selected law (with a single batched Neo4j call for graph context, not one per section), the
query itself is classified by the Policy Engine, then Groq's hosted API generates a streamed,
cited answer - checked post-generation for any citation the model invented that wasn't actually
retrieved - UNLESS the question is high-risk for that law (penalty, obligation, breach,
beneficial owner, CERT-In, etc.), in which case it's held for human review instead of answered
directly
   |
   v
Static HTML/JS/CSS chat UI with a law selector - deployed as its own Render Static Site, calling
the backend's public API URL directly
```

## Supported laws

| Code | Law | Pillar | Extraction method |
|---|---|---|---|
| `dpdp` | Digital Personal Data Protection Act, 2023 | Privacy | Regex-based, sequential-anchor split (fast, auto-ingests at startup) |
| `gdpr` | General Data Protection Regulation (EU) 2016/679 - full text, 99 Articles | Privacy | Regex-based, sequential-anchor split (fast, on-demand) |
| `kyc_aml` | RBI Master Direction on KYC (incl. CDD, Beneficial Owner) | RBI/KYC | LLM-based (Groq, ~5-6 min, on-demand) |
| `pmla` | Prevention of Money Laundering Act, 2002 + Rules | RBI/KYC | Header-split on clean HTML (fast, on-demand) |
| `rbi_cyber` | RBI Cyber Security Framework + CERT-In Incident Reporting Rules | Cybersecurity | LLM-based (Groq, ~5-6 min, on-demand) |
| `irdai` | IRDAI (Protection of Policyholders' Interests) Regulations, 2017 | Insurance | Header-split on clean HTML, sequential-anchor (fast, on-demand) |

Only `dpdp` auto-ingests on startup. Trigger the other five with `POST /ingest/{law_code}`
(admin key required) and poll `GET /laws` or `GET /health` for completion.

## Tech stack

| Component | Tool | Role |
|---|---|---|
| Knowledge graph | Neo4j AuraDB (Free tier, hosted) | Sections linked to obligations, rights, penalties, definitions, plus data_classes/required_controls; graph context for all retrieved sections is fetched in one batched query, not one per section |
| Retrieval | TF-IDF cosine similarity + MMR/exact-phrase reranking (pure Python) | Lexical search over section text, reranked for diversity and phrase precision - fits a 512MB RAM budget, one index per law |
| LLM | Groq hosted API (Llama 3.3 70B by default) | Generates cited answers; also powers KYC/Cyber section extraction |
| Data Classification | `policy_engine.py` (rule-based, keyword matching) | Tags text against customer_pii / financial_data / transaction_data / sensitive_data |
| Policy enforcement | `crypto_utils.py` (regex masking, Fernet encryption, token vault) | Actually applies masking/encryption/tokenisation, not just recommends them - exposed via `POST /protect` |
| Audit trail | `audit_log.py` (SQLite, append-only, trigger-enforced) | Query log, review-decision log, ingestion-classification log |
| Backend | FastAPI + Uvicorn, deployed on Render (Python, free tier) | REST + streaming NDJSON API, with per-request retrieval/generation timing in every `/ask` response |
| Frontend | Static HTML/CSS/JS, deployed as a separate Render Static Site | Chat UI with law selector + live human-review sidebar + Data Classification panel |
| Testing | `pytest` (`tests/`) | Unit + integration coverage for retrieval, policy engine, crypto utilities, ingestion parsing, and API bug-fix regressions |
| Source data | Official government/EU texts (MeitY, RBI, FIU-IND, CERT-In, EUR-Lex mirror, IRDAI mirror) | Fetched live at ingestion time, not hardcoded |

Backend and frontend are two independent Render deployments with different URLs; the frontend
calls the backend's API URL directly (CORS via `ALLOWED_ORIGIN`).

## Deployment

**Backend (FastAPI, Python runtime), from repo root:**
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Required environment variables:

  | Variable | Purpose |
  |---|---|
  | `GROQ_API_KEY` | Free key from [console.groq.com/keys](https://console.groq.com/keys) |
  | `GROQ_MODEL` | e.g. `llama-3.3-70b-versatile` - check [console.groq.com/docs/models](https://console.groq.com/docs/models) for current availability |
  | `NEO4J_URI` | From your Neo4j AuraDB instance, e.g. `neo4j+s://xxxxx.databases.neo4j.io` |
  | `NEO4J_USER` | Usually `neo4j` |
  | `NEO4J_PASSWORD` | Shown once at AuraDB instance creation - save it immediately |
  | `ALLOWED_ORIGIN` | `*` for a public demo, or your exact frontend URL to lock it down |
  | `ADMIN_API_KEY` | **Required for real deployments.** Protects `POST /ingest/{law}` and `POST /approve-review-item`. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Without this set, those endpoints run with auth disabled and a startup warning is logged. |
  | `POLICY_ENCRYPTION_KEY` | Optional. Without it, a random key is generated per process and anything encrypted via `/protect` won't decrypt after a restart. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |

**Frontend (static site):**
- Build command: none needed (plain HTML/CSS/JS)
- Publish directory: `frontend/`
- `app.js` has the backend's public URL set directly in the `API` constant near the top of the
  file - update this to match your own backend's URL if you fork/redeploy this project.
- The sidebar's law selector and admin-key input are injected by `app.js` at runtime.

## Human-in-the-loop: two checkpoints

**Checkpoint 1 - before anything enters the graph/search index.** Every parsed section (for all
six laws) is tagged by category and run through the Data Classification / Policy Engine. Any
section touching an Obligation/Penalty, with low parse confidence, failing an LLM verbatim check,
or matching the `sensitive_data` classification, is held out of the graph and search index until
a human approves it through the review sidebar (`POST /approve-review-item`, admin key required).

**Checkpoint 2 - before a generated answer is shown.** Questions containing law-specific high-risk
keywords (penalty, obligation, breach, beneficial owner, CERT-In, 6 hours, etc. - see
`banking_config.LAWS[law]["high_risk_query_keywords"]`) never get an auto-generated answer - they
come back `pending_review` with the retrieved context attached, for a human to check before
anything resembling advice reaches an end user.

## Data Classification + Policy Engine

`policy_engine.py` classifies any text (an ingested regulatory section, or a live user query)
against four data classes and recommends the controls each one requires:

| Data class | Example keywords | Recommended controls |
|---|---|---|
| `customer_pii` | name, PAN, Aadhaar, phone, email, photograph | masking, encryption |
| `financial_data` | account balance, income, credit score, loan amount | encryption, tokenisation |
| `transaction_data` | UTR, IFSC, UPI ID, card number, transaction amount | tokenisation, encryption |
| `sensitive_data` | biometric, health, religion, caste, criminal record | encryption, masking, tokenisation |

- `POST /classify` and `POST /evaluate` are advisory-only and fast - they tell you what a piece
  of text needs, with a rationale.
- `POST /protect` actually applies the recommended controls via `crypto_utils.py` and returns the
  transformed values (masked text, an encrypted token, a tokenised surrogate).
- Every ingested section's classification is logged to `ingestion_policy_log` (see
  `GET /audit-log/ingestion`); every query's classification is logged alongside the query itself
  in `query_log`.

## Configuration reference

Per-law tunables live in `banking_config.LAWS[<code>]`; shared defaults for DPDP specifically are
also in `dpdp_config.py`:

| Setting | Default | Meaning |
|---|---|---|
| `confidence_threshold` | 0.85 | Below this, a parsed section goes to human review regardless of category |
| `sensitive_categories` | varies per law | Categories that always require human review before indexing |
| `high_risk_query_keywords` | varies per law | Trigger human review on the *question* itself, not just the source content |
| `retrieval_top_k` | 5 | Sections retrieved per query |
| `similarity_threshold` | 0.12 | Below this, a result is treated as a weak match |
| `hard_cutoff` | 0.04 | Below this, a result is discarded entirely |

TF-IDF cosine similarity scores run on a different scale than sentence-transformer embedding
similarity - these thresholds are tuned for TF-IDF specifically, which is purely lexical and so
naturally stricter about wording than semantic embeddings.

## API reference (high-level)

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | none | Ingestion status per law, Neo4j connectivity, whether admin auth is configured |
| `/laws` | GET | none | List all laws with pillar + ingestion status |
| `/stats?law=` | GET | none | Section counts (total/auto-approved/pending/approved/rejected/indexed) for one law |
| `/ask` | POST | none (rate-limited) | Streaming NDJSON chat answer for `{query, law}` |
| `/pending-review?law=` | GET | none | Sections awaiting human review for one law |
| `/section/{id}?law=` | GET | none | Full text + classification for one section |
| `/approve-review-item?law=` | POST | `X-Admin-Key` | Approve/reject a pending section |
| `/ingest/{law_code}` | POST | `X-Admin-Key` | Trigger ingestion for one law (background task) |
| `/classify` | POST | none | Advisory Data Classification for arbitrary text |
| `/evaluate` | POST | none | Classification + recommended controls, with rationale |
| `/protect` | POST | none | Classification + controls **actually applied** (masked/encrypted/tokenised output) |
| `/audit-log?law=` | GET | none | Query audit trail |
| `/audit-log/reviews` | GET | none | Review-decision audit trail |
| `/audit-log/ingestion?law=` | GET | none | Ingestion-time classification audit trail |

Full interactive docs at `/docs` (Swagger UI).

## RAG pipeline engineering notes

What actually happens between a question coming in and an answer going
out, in order:

1. **Retrieval (TF-IDF, in-memory).** Every ingested section is a
   document in a per-law TF-IDF index (`src/tfidf_search.py`). No
   external vector DB or embedding API - keeps this fully within Render's
   and Neo4j AuraDB's free tiers.
2. **Reranking (`search_reranked`).** Raw cosine similarity pulls a wider
   candidate pool, then two cheap, zero-cost passes reorder it before
   truncating to `retrieval_top_k`:
   - an **exact-phrase bonus** for a section containing the literal query
     string, not just its scattered terms;
   - **MMR (Maximal Marginal Relevance)** diversity selection, so a small
     top-k doesn't fill up with several near-duplicate sections at the
     expense of a genuinely different but still-relevant one.
3. **Smart context windowing (`_best_context_window`).** A section longer
   than `context_char_limit` used to be truncated from character 0,
   silently dropping the actually-relevant part if it was buried deeper
   in. It's now centered on the highest query-term-density window inside
   the section instead - a lightweight, dependency-free stand-in for
   query-aware chunking.
4. **Graph context, batched.** `graph_context_batch()` fetches every
   retrieved section's Neo4j-linked entities in a single `UNWIND` query
   instead of opening one session per section - collapses what used to
   be up to `retrieval_top_k` sequential round trips into one.
5. **Groundedness gate.** A query scoring below `hard_cutoff` returns
   the law's `not_found_note` instead of going to the LLM at all - no
   context, no generation, no chance to hallucinate an answer from
   nothing.
6. **Generation (Groq, streamed).** The system prompt restricts the
   model to the numbered `[S<n>]` context sections only and gives it an
   explicit refusal string to use when the context is insufficient.
7. **Citation faithfulness check (`_check_citation_faithfulness`),
   post-generation.** A system prompt is an instruction, not a guarantee.
   This scans the generated answer for any `[S<n>]`-style citation and
   flags any that don't correspond to a section actually retrieved and
   placed in context - a deterministic, zero-extra-latency check that
   doesn't require a second LLM call to enforce.
8. **Per-stage timing, surfaced to the client.** The final `/ask` NDJSON
   `"done"` event includes a `timings` object
   (`retrieval_ms` / `graph_ms` / `generation_ms` / `total_ms`), not just
   logged server-side - so latency is inspectable per request, not just
   in aggregate.

### Testing

`tests/` covers the pure-Python pieces with no live network/DB
dependency: TF-IDF ranking and reranking, the Policy Engine's
classification rules, the masking/encryption/tokenisation round-trips in
`crypto_utils.py`, the sequential-anchor section-splitting logic for
GDPR/IRDAI (regression tests for two false-match failure modes found
during development), and the `main.py` bug fixes (approve-review-item,
ingestion audit logging, citation faithfulness, smart context window).

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## Known limitations

- **TF-IDF is lexical, not semantic** - it can miss relevant sections when a question uses very
  different wording than the source text, even if the underlying meaning matches. Moving to
  embeddings-based semantic search needs more RAM than Render's free tier gives a Python process
  running FastAPI + pypdf + a sentence-transformers model at once.
- **KYC and Cyber ingestion (`llm_ingest.py`) has not been run end-to-end with live network
  access** - the pure logic is tested, but the actual Groq calls, PDF layout handling, and
  verbatim-overlap behavior on the real source documents need a live run before production use.
- **GDPR and IRDAI are the newest additions and have less production mileage than DPDP** - the
  section-splitting logic is unit-tested (see `tests/test_ingest_splitting.py`) against realistic
  synthetic text, but hasn't yet had a long-running ingestion against the live source documents
  monitored for edge cases the tests didn't anticipate.
- **PMLA coverage is a curated subset**, not the exhaustive Sections 1-75, per FIU-IND's own
  published extract.
- **The tokenisation vault and Fernet key are in-memory / per-process** - a real deployment needs
  a persistent, access-controlled vault and a `POLICY_ENCRYPTION_KEY` set in the environment, not
  the auto-generated fallback.
- **SQLite audit log is not durable on Render's free tier** - the filesystem is ephemeral across
  cold restarts, and state isn't shared across horizontally-scaled instances. Point `DB_PATH` at
  a persistent volume or move to Postgres before relying on this as a real compliance record.
- **Both free-tier services (Render web service and Neo4j AuraDB Free) can spin down or pause
  after inactivity**, adding latency (up to 50+ seconds) to the first request after idle periods.
- **The citation faithfulness check catches invented citation *numbers*, not subtler unsupported
  claims** - a model could still generate a plausible-sounding but ungrounded sentence attached to
  a *real* citation. A stronger guard would compare each generated sentence's claims against its
  cited section's actual text (e.g. via NLI-style entailment), which needs a model call this
  pipeline doesn't currently make.

## Next steps for a production system

1. Run KYC/Cyber ingestion live at least once and validate output quality before trusting it in
   a human-review workflow.
2. Move the tokenisation vault and audit log to durable, access-controlled storage (Postgres or
   equivalent) instead of in-memory/SQLite-on-ephemeral-disk.
3. Add real authentication (per-reviewer accounts) in place of the current single shared
   `X-Admin-Key` and free-text `reviewer` field.
4. Also ingest the Digital Personal Data Protection Rules, 2025 (notified 13 November 2025) and
   the exhaustive PMLA sections 1-75.
5. Move retrieval from TF-IDF to embeddings-based semantic search once a paid tier or higher-RAM
   host removes the memory constraint that motivated the TF-IDF trade-off.
6. Strengthen the citation faithfulness check into a claim-level entailment check, not just a
   citation-number existence check (see Known limitations).
7. Repeat this pattern per country to build out a multi-jurisdiction policy graph.

## Sources

- DPDP Act, 2023 - Ministry of Electronics and Information Technology (MeitY):
  https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf
- General Data Protection Regulation (EU) 2016/679 - full official text, mirrored at
  https://gdpr.eu.org/full/full.pdf (EUR-Lex's own PDF endpoint returns an empty body for
  automated requests - see `src/gdpr_ingest.py` for the sourcing note).
- RBI Master Direction on KYC - fetched from rbidocs.rbi.org.in; verify the current URL at
  rbi.org.in before ingesting, as RBI reissues master directions with new document IDs
  periodically.
- PMLA, 2002 + Rules - Financial Intelligence Unit - India (FIU-IND): https://fiuindia.gov.in
- RBI Cyber Security Framework + CERT-In Directions - CERT-In: https://www.cert-in.org.in
- IRDAI (Protection of Policyholders' Interests) Regulations, 2017 - mirrored at
  taxguru.in (irdai.gov.in's own document pages block automated fetches - see
  `src/irdai_ingest.py` for the sourcing note and a caution about re-verifying this URL
  periodically).



This project is for educational/demonstration purposes. All source regulatory texts are
government works; this repository's code is provided as-is for learning and research. This
assistant provides informational summaries only, not legal or compliance advice.
