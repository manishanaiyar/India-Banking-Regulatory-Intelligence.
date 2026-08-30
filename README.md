# India Banking Regulatory Intelligence - GraphRAG Assistant with Human-in-the-Loop Review

A production deployment of a policy knowledge-graph and retrieval-augmented assistant covering
**four Indian regulatory frameworks** across three compliance pillars - Privacy, RBI/KYC, and
Cybersecurity - each with its own knowledge graph, retrieval index, and human-review queue.
Deployed on free-tier cloud infrastructure. No local setup required to use it.

**Live demo:** https://india-dpdp-act-graphrag-assistant-with-w6t0.onrender.com
**API:** https://india-dpdp-act-graphrag-assistant-with.onrender.com/docs

## What this is

This started as a single-law DPDP Act assistant and grew into a multi-law regulatory
intelligence platform. It combines: a **knowledge graph per law** connecting sections to their
obligations, rights, and penalties; a **lightweight lexical search index** for retrieval over
each law's text; a **hosted LLM** that retrieves and generates cited answers; a **Data
Classification + Policy Engine** that tags both ingested regulatory text and live queries against
a customer-data taxonomy and recommends (and can actually apply) masking/encryption/tokenisation;
a **human-in-the-loop review gate** so nothing unverified reaches an end user; and an **append-only
audit trail** covering queries, review decisions, and ingestion-time classifications. Packaged as
a FastAPI backend and a static HTML/JS frontend, deployed independently.

## Architecture

```
        RBI/KYC              Privacy              Cybersecurity
     KYC/AML, PMLA,        DPDP Act, 2023      RBI Cyber Security
     CDD, Beneficial        (Consent,           Framework + CERT-In
     Owner                  Retention,          Incident Reporting
                             Purpose)
          |                     |                      |
          +----------+----------+----------+-----------+
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
Parse into numbered sections (regex for DPDP/PMLA, LLM-based extraction for KYC/Cyber)
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
On a question: TF-IDF cosine-similarity search retrieves the most relevant sections for the
selected law, the query itself is classified by the Policy Engine, then Groq's hosted API
generates a streamed, cited answer - UNLESS the question is high-risk for that law (penalty,
obligation, breach, beneficial owner, CERT-In, etc.), in which case it's held for human review
instead of answered directly
   |
   v
Static HTML/JS/CSS chat UI with a law selector - deployed as its own service, calling
the backend's public API URL directly
```

## Supported laws

| Code | Law | Pillar | Extraction method |
|---|---|---|---|
| `dpdp` | Digital Personal Data Protection Act, 2023 | Privacy | Regex-based (fast, auto-ingests at startup) |
| `kyc_aml` | RBI Master Direction on KYC (incl. CDD, Beneficial Owner) | RBI/KYC | LLM-based (Groq, ~5-6 min, on-demand) |
| `pmla` | Prevention of Money Laundering Act, 2002 + Rules | RBI/KYC | Header-split on clean HTML (fast, on-demand) |
| `rbi_cyber` | RBI Cyber Security Framework + CERT-In Incident Reporting Rules | Cybersecurity | LLM-based (Groq, ~5-6 min, on-demand) |

Only `dpdp` auto-ingests on startup. Trigger the other three with `POST /ingest/{law_code}`
(admin key required) and poll `GET /laws` or `GET /health` for completion.

## Tech stack

| Component | Tool | Role |
|---|---|---|
| Knowledge graph | Neo4j AuraDB (Free tier, hosted) | Sections linked to obligations, rights, penalties, definitions, plus data_classes/required_controls |
| Retrieval | TF-IDF cosine similarity (pure Python) | Lexical search over section text - fits a 512MB RAM budget, one index per law |
| LLM | Groq hosted API (Llama 3.3 70B by default) | Generates cited answers; also powers KYC/Cyber section extraction |
| Data Classification | `policy_engine.py` (rule-based, keyword matching) | Tags text against customer_pii / financial_data / transaction_data / sensitive_data |
| Policy enforcement | `crypto_utils.py` (regex masking, Fernet encryption, token vault) | Actually applies masking/encryption/tokenisation, not just recommends them |
| Audit trail | `audit_log.py` (SQLite, append-only, trigger-enforced) | Query log, review-decision log, ingestion-classification log |
| Backend | FastAPI + Uvicorn, deployed on Render (Python, free tier) | REST + streaming NDJSON API |
| Frontend | Static HTML/CSS/JS, deployed separately (Vercel/Render Static) | Chat UI with law selector + live human-review sidebar |
| Source data | Official government PDFs/HTML (MeitY, RBI, FIU-IND, CERT-In) | Fetched live at ingestion time, not hardcoded |

Backend and frontend are two independent deployments with different URLs; the frontend calls the
backend's API URL directly (CORS via `ALLOWED_ORIGIN`).

## Deployment

**Backend (FastAPI, Python runtime), from repo root:**
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn src.main:app --host 0.0.0.0 --port $PORT`
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
four laws) is tagged by category and run through the Data Classification / Policy Engine. Any
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

## Known limitations

- **KYC and Cyber ingestion (`llm_ingest.py`) has not been run end-to-end with live network
  access** - the pure logic is tested, but the actual Groq calls, PDF layout handling, and
  verbatim-overlap behavior on the real source documents need a live run before production use.
- **PMLA coverage is a curated subset**, not the exhaustive Sections 1-75, per FIU-IND's own
  published extract.
- **TF-IDF is lexical, not semantic** - it can miss relevant sections when a question uses very
  different wording than the source text, even if the underlying meaning matches.
- **The tokenisation vault and Fernet key are in-memory / per-process** - a real deployment needs
  a persistent, access-controlled vault and a `POLICY_ENCRYPTION_KEY` set in the environment, not
  the auto-generated fallback.
- **SQLite audit log is not durable on Render's free tier** - the filesystem is ephemeral across
  cold restarts, and state isn't shared across horizontally-scaled instances. Point `DB_PATH` at
  a persistent volume or move to Postgres before relying on this as a real compliance record.
- **Both free-tier services (Render web service and Neo4j AuraDB Free) can spin down or pause
  after inactivity**, adding latency (up to 50+ seconds) to the first request after idle periods.
- **No automated test suite yet.** `policy_engine.py`, `crypto_utils.py`, and `llm_ingest.py`'s
  pure functions (`chunk_text`, `verbatim_overlap_ratio`, `split_pmla_sections`) are all
  side-effect-free and would be cheap to cover with `pytest`.

## Next steps for a production system

1. Run KYC/Cyber ingestion live at least once and validate output quality before trusting it in
   a human-review workflow.
2. Move the tokenisation vault and audit log to durable, access-controlled storage (Postgres or
   equivalent) instead of in-memory/SQLite-on-ephemeral-disk.
3. Add real authentication (per-reviewer accounts) in place of the current single shared
   `X-Admin-Key` and free-text `reviewer` field.
4. Add automated tests, especially for the Policy Engine, crypto utilities, and ingestion parsing
   logic.
5. Also ingest the Digital Personal Data Protection Rules, 2025 (notified 13 November 2025) and
   the exhaustive PMLA sections 1-75.
6. Move retrieval from TF-IDF to embeddings-based semantic search once a paid tier or higher-RAM
   host removes the memory constraint that motivated the TF-IDF trade-off.
7. Repeat this pattern per country to build out a multi-jurisdiction policy graph.

## Sources

- DPDP Act, 2023 - Ministry of Electronics and Information Technology (MeitY):
  https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf
- RBI Master Direction on KYC - verify the current URL at rbi.org.in before ingesting; RBI
  reissues master directions with new document IDs periodically.
- PMLA, 2002 + Rules - Financial Intelligence Unit - India (FIU-IND): https://fiuindia.gov.in
- RBI Cyber Security Framework + CERT-In Directions - CERT-In: https://www.cert-in.org.in



This project is for educational/demonstration purposes. All source regulatory texts are
government works; this repository's code is provided as-is for learning and research. This
assistant provides informational summaries only, not legal or compliance advice.
