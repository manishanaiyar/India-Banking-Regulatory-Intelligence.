# India DPDP Act - GraphRAG Assistant with Human-in-the-Loop Review

A production deployment of a policy knowledge-graph and retrieval-augmented assistant, scoped to
a single law - India's **Digital Personal Data Protection Act, 2023** - so the full pipeline is easy
to run, explain, and verify end to end. Deployed on free-tier cloud infrastructure. No local setup
required to use it.

**Live demo:** https://india-dpdp-act-graphrag-assistant-with-w6t0.onrender.com
**API:** https://india-dpdp-act-graphrag-assistant-with.onrender.com/docs

## What this is

A demo of: a **knowledge graph** connecting a law to its obligations, rights, and penalties; a
**lightweight lexical search index** for retrieval over the legal text; a **hosted LLM** that
retrieves and generates cited answers; a **human-in-the-loop review gate** so nothing unverified
reaches an end user; and a chat interface for people to actually use it - packaged as a FastAPI
backend and a static HTML/JS frontend, each deployed independently.

## Architecture

```
Official DPDP Act PDF (fetched live from meity.gov.in)
        |
        v
Parse into 44 numbered sections (regex, validated against the real text)
        |
        v
Rule-based tagging: category (Obligation / Right / Penalty / Definition) + confidence
        |
        v
  +-----+-----------------------------------+
  |                                         |
  v                                         v
Auto-approved                      Held for HUMAN REVIEW
(safe category, high confidence)   (touches Obligation/Penalty, or low confidence)
  |                                         |
  v                                         v
 Indexed for TF-IDF search              Sits in a review queue until a human
 + written to Neo4j (graph)             approves/rejects it via the sidebar -
  |                                      only then is it indexed and written
  |                                      to the graph too
  v
FastAPI backend (/ask, /pending-review, /health, /stats)
  |
  v
On a question: TF-IDF cosine-similarity search retrieves the most relevant
sections, then Groq's hosted API (openai/gpt-oss-120b) generates a
streamed, cited answer - UNLESS the question itself is high-risk
(penalty/obligation/cross-border), in which case it's also held for
human review instead of answered directly
  |
  v
Static HTML/JS/CSS chat UI  --->  deployed as its own service, calling
                                   the backend's public API URL directly
```

## Tech stack

| Component | Tool | Role |
|---|---|---|
| Knowledge graph | Neo4j AuraDB (Free tier, hosted) | Sections linked to obligations, rights, penalties, definitions |
| Retrieval | TF-IDF cosine similarity (`scikit-learn`) | Lexical search over section text - lighter-weight than embeddings, fits a 512MB RAM budget |
| LLM | Groq hosted API, `openai/gpt-oss-120b` | Generates the final cited answer text, streamed over SSE |
| Backend | FastAPI + Uvicorn, deployed on Render (Python 3, free tier) | REST API: `/ask`, `/pending-review`, `/health`, `/stats` |
| Frontend | Static HTML/CSS/JS, deployed on Render (Static Site) | Chat interface + live human-review sidebar |
| Source data | Official Gazette PDF, [MeitY](https://www.meity.gov.in/) | Fetched live at runtime, not hardcoded |

Backend and frontend are deployed as two independent Render services with different URLs; the
frontend calls the backend's API URL directly (CORS is handled via the `ALLOWED_ORIGIN` env var).

## Deployment

Both services deploy from this same repository.

**Backend (FastAPI, Python runtime):**
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Root directory: repo root (not a subfolder)
- Required environment variables:
  | Variable | Purpose |
  |---|---|
  | `GROQ_API_KEY` | Free key from [console.groq.com/keys](https://console.groq.com/keys) |
  | `GROQ_MODEL` | `openai/gpt-oss-120b` (see note below on model choice) |
  | `NEO4J_URI` | From your Neo4j AuraDB instance, e.g. `neo4j+s://xxxxx.databases.neo4j.io` |
  | `NEO4J_USER` | Usually `neo4j` |
  | `NEO4J_PASSWORD` | Shown once at AuraDB instance creation - save it immediately |
  | `ALLOWED_ORIGIN` | `*` for a public demo, or your exact frontend URL to lock it down |

**Frontend (static site):**
- Build command: none needed (plain HTML/CSS/JS)
- Publish directory: repo root
- `app.js` has the backend's public URL set directly in the `API` constant near the top of the
  file - update this to match your own backend's URL if you fork/redeploy this project.

### A note on the LLM model

`openai/gpt-oss-120b` is a **reasoning model**: Groq streams its internal reasoning separately from
the final answer, and by default spends a chunk of the token budget on that reasoning before writing
the answer. If you see empty responses, check two things in `dpdp_config.py` / `groq_client.py`:
`GROQ_MAX_TOKENS` needs enough headroom for reasoning *and* the answer (1024 is a safe floor), and
the request payload should set `"reasoning_effort": "low"` to keep the reasoning budget small. Groq
has deprecated several older Llama models over time - check
[console.groq.com/docs/models](https://console.groq.com/docs/models) if you hit a `model_not_found`
error and need to pick a current replacement.

## Human-in-the-loop: two checkpoints

**Checkpoint 1 - before anything enters the graph/search index.** Every parsed section is tagged by
chapter and title. Any section touching an Obligation or Penalty, or with a low parse-confidence
score, is held out of the graph and search index until a human approves it through the review
sidebar.

**Checkpoint 2 - before a generated answer is shown.** Questions containing high-risk keywords
(penalty, obligation, breach, cross-border transfer) never get an auto-generated answer - they come
back `pending_review` with the retrieved context attached, for a human to check before anything
resembling advice reaches an end user.

## Configuration reference

Key tunables live in `dpdp_config.py`:

| Setting | Default | Meaning |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | 0.85 | Below this, a parsed section goes to human review regardless of category |
| `SENSITIVE_CATEGORIES` | obligations, penalties | Categories that always require human review before indexing |
| `HIGH_RISK_QUERY_KEYWORDS` | penalty, fine, breach, obligation, cross-border, etc. | Trigger human review on the *question* itself, not just the source content |
| `RETRIEVAL_TOP_K` | 5 | Sections retrieved per query |
| `SIMILARITY_THRESHOLD` | 0.12 | Below this, a result is treated as a weak match |
| `HARD_CUTOFF` | 0.04 | Below this, a result is discarded entirely |

Note: TF-IDF cosine similarity scores run on a different scale than sentence-transformer embedding
similarity - these thresholds are tuned for TF-IDF specifically. TF-IDF is purely lexical (keyword
overlap), so it's naturally stricter about wording than a semantic embedding would be: a heavily
paraphrased question may score lower than it would with embeddings, even when genuinely in scope.

## Known limitations

- **Tagging is rule-based** (chapter/title keyword matching), not LLM-based, for reliability and
  speed - see "Next steps" below for how a real extraction agent would replace this.
- **Only the original 2023 Act text is covered** - the Digital Personal Data Protection Rules, 2025
  (notified 13 November 2025) are not included.
- **TF-IDF is lexical, not semantic** - it can miss relevant sections when a question uses very
  different wording than the source text, even if the underlying meaning matches.
- **Both free-tier services (Render web service and Neo4j AuraDB Free) can spin down or pause
  after inactivity**, adding latency (up to 50+ seconds) to the first request after idle periods.

## Next steps for a production system

1. Replace rule-based tagging with a real LLM extraction agent run as an offline batch job (not
   blocking server startup) - the review-queue mechanics here plug in directly, only the source of
   the confidence score changes.
2. Also ingest the Digital Personal Data Protection Rules, 2025.
3. Add amendment monitoring: stage detected law changes for human confirmation before they update
   the graph.
4. Repeat this same pattern per country to build out a full multi-jurisdiction policy graph
   (GDPR, CCPA, LGPD, PIPL, and 70+ other jurisdictions).
5. Move retrieval from TF-IDF to embeddings-based semantic search once a paid tier or higher-RAM
   host removes the memory constraint that motivated the TF-IDF trade-off.

## Source

Official Gazette of India, Ministry of Law and Justice, 11 August 2023, published by the Ministry of
Electronics and Information Technology (MeitY):
https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf

## License

This project is for educational/demonstration purposes. The DPDP Act text is a government work; this
repository's code is provided as-is for learning and research.
