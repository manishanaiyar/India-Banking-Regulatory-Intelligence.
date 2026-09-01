"""
DPDP Act + Banking Regulatory Intelligence GraphRAG service - Render/Groq/AuraDB build.

Architecture this file wires together (see project diagram):

    RBI/KYC + Privacy(DPDP) + Cybersecurity  -->  DATA CLASSIFICATION
                                              -->  POLICY ENGINE (mask/encrypt/tokenise)
                                              -->  AUDIT & MONITORING

Four laws, each with its OWN KnowledgeStore and ReviewQueue instance
(see `_stores` / `_review_queues` below), so DPDP's behavior is
unaffected by the new laws: same TF-IDF index, same Neo4j writes, same
review queue object as before. New laws (kyc_aml, pmla, rbi_cyber) are
additive.

Startup only auto-ingests DPDP (see run_ingestion()). KYC/Cyber use an
LLM-based extraction pipeline (llm_ingest.py) that is SLOW - roughly
5-6 minutes end-to-end per document, because of Groq free-tier rate
limiting (SLEEP_BETWEEN_CALLS_SECONDS = 65 between chunks). That is far
longer than Render's request timeout, so it must never run synchronously
inside a request handler. This file runs it as a FastAPI BackgroundTask
instead: POST /ingest/{law_code} returns immediately with
status="started", and the caller polls GET /health or GET /laws for
completion. PMLA has no such restriction (pure header-split, no LLM
call) but is routed through the same background path for a uniform API.

CHANGES FROM THE PREVIOUS VERSION (all deliberate fixes, listed so
nothing here is a silent surprise):

  1. Audit logging on cache hits. Previously, a cached /ask answer
     returned early and skipped audit_log.log_query() entirely, so
     repeat queries had no audit trail. Every terminal state - cached,
     no_answer, pending_review, answered - now logs exactly once.

  2. Non-blocking ingestion for kyc_aml/rbi_cyber (and pmla, for
     consistency). Previously /ingest/{law_code} called
     run_ingestion_for_law() directly inside the request handler, which
     for the LLM-based laws blocked for minutes and would time out
     mid-Groq-call, burning API quota with no recorded progress and no
     way to know the run had started. It's now scheduled as a
     BackgroundTask; an in-progress flag prevents duplicate concurrent
     runs for the same law.

  3. Admin-key auth on mutating endpoints. POST /ingest/{law_code} and
     POST /approve-review-item previously had no auth at all - anyone
     could trigger ingestion or approve/reject human-review items,
     which defeats the point of a human-in-the-loop review queue. Both
     now require an `X-Admin-Key` header matching ADMIN_API_KEY (read
     from dpdp_config, falling back to an env var of the same name if
     dpdp_config doesn't define it). If no admin key is configured at
     all, the app still runs (so local/dev use isn't blocked) but logs
     a loud warning at startup and on every protected call - fix this
     before any real deployment.

  4. Rate limiter now prefers X-Forwarded-For. Behind Render's proxy,
     http_request.client.host is the proxy's own IP for every request,
     so the old code effectively rate-limited "everyone" as one client
     (or nobody meaningfully). It now uses the first address in
     X-Forwarded-For when present, falling back to client.host.

  5. Removed the unused `import time` at module level (dead import).

The frontend is a SEPARATE deployment (Vercel) - this process serves
only the JSON/NDJSON API, no static files.
"""

import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src import dpdp_config as cfg
from src.dpdp_ingest import build_tagged_sections, fetch_act_pdf
from src.dpdp_stores import AnswerCache, KnowledgeStore, RateLimiter, ReviewQueue
from src.groq_client import GroqError, stream_chat
from src import tfidf_search

from src import banking_config
from src import policy_engine
from src import audit_log
from src.ingest_common import IngestError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dpdp.main")

# ---------------------------------------------------------------------------
# Admin auth - see fix #3 above.
# ---------------------------------------------------------------------------
ADMIN_API_KEY = getattr(cfg, "ADMIN_API_KEY", None)
if not ADMIN_API_KEY:
    logger.warning(
        "ADMIN_API_KEY is not set - POST /ingest/{law} and POST /approve-review-item "
        "are running WITHOUT AUTH. Set ADMIN_API_KEY in your environment before any "
        "real deployment; the human-in-the-loop review queue is not protected without it."
    )


def require_admin(x_admin_key: Optional[str] = Header(default=None)) -> None:
    if not ADMIN_API_KEY:
        return  # dev mode - warning already logged at startup
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Admin-Key header.")


# ---------------------------------------------------------------------------
# DPDP - own store/queue, exactly as the original single-law app.
# ---------------------------------------------------------------------------
store = KnowledgeStore()
review_queue = ReviewQueue()
answer_cache = AnswerCache()
rate_limiter = RateLimiter(cfg.RATE_LIMIT_REQUESTS_PER_MINUTE)

_ingestion_done = False
_ingestion_error: Optional[str] = None


def run_ingestion() -> None:
    """Idempotent DPDP ingestion. Fast enough (regex-based, single PDF) to
    run inline at startup - unlike the LLM-based laws below."""
    global _ingestion_done, _ingestion_error
    if _ingestion_done:
        return

    try:
        pdf_path = fetch_act_pdf(
            max_retries=cfg.PDF_FETCH_RETRIES, backoff_seconds=cfg.PDF_FETCH_BACKOFF_SECONDS,
        )
        sections = build_tagged_sections(pdf_path, cfg.MAX_SECTIONS)

        auto_approved = 0
        for section in sections:
            needs_review = section["sensitive"] or section["confidence"] < cfg.CONFIDENCE_THRESHOLD
            review_queue.register(section, needs_review)
            if not needs_review:
                store.commit_section(section)
                auto_approved += 1
            audit_log.log_ingestion_classification(
                law_code="dpdp", section_id=section["id"],
                data_classes=section.get("data_classes"),
                required_controls=section.get("required_controls"),
            )

        logger.info(
            "DPDP ingestion complete: %d/%d auto-approved and indexed, %d pending human review.",
            auto_approved, len(sections), len(sections) - auto_approved,
        )
        _ingestion_error = None
        _ingestion_done = True
    except Exception as exc:
        logger.exception("DPDP ingestion failed")
        _ingestion_error = str(exc)


# ---------------------------------------------------------------------------
# KYC/AML, PMLA, RBI Cyber - own store + review queue each, created LAZILY
# (only when that law's ingestion is actually triggered), so a deploy that
# only ever serves DPDP traffic never opens more than one Neo4j connection.
# All instances point at the SAME AuraDB database; ingest_common.py /
# llm_ingest.py namespace every section id as "<law_code>:<section_number>"
# to avoid collisions with DPDP's own "S1".."S44" ids.
# ---------------------------------------------------------------------------
NEW_LAW_CODES = ("kyc_aml", "pmla", "rbi_cyber", "gdpr", "irdai")

_stores: dict[str, KnowledgeStore] = {"dpdp": store}
_review_queues: dict[str, ReviewQueue] = {"dpdp": review_queue}
_other_law_status: dict[str, dict] = {
    law: {"done": False, "error": None, "in_progress": False} for law in NEW_LAW_CODES
}


def _get_store(law: str) -> KnowledgeStore:
    if law not in _stores:
        logger.info("Lazily creating KnowledgeStore (+ Neo4j driver connection) for law=%s", law)
        _stores[law] = KnowledgeStore()
    return _stores[law]


def _get_review_queue(law: str) -> ReviewQueue:
    if law not in _review_queues:
        _review_queues[law] = ReviewQueue()
    return _review_queues[law]


def _ingestion_status_for(law: str) -> tuple[bool, Optional[str]]:
    if law == "dpdp":
        return _ingestion_done, _ingestion_error
    status = _other_law_status[law]
    return status["done"], status["error"]


def _run_ingestion_for_law_background(law: str) -> None:
    """Runs in a FastAPI BackgroundTask - see fix #2. Never call this
    synchronously inside a request handler for kyc_aml/rbi_cyber; the
    LLM-based extraction in llm_ingest.py can take several minutes."""
    if law == "dpdp":
        run_ingestion()
        return

    status = _other_law_status[law]
    if status["done"] or status["in_progress"]:
        return
    status["in_progress"] = True
    try:
        if law == "kyc_aml":
            from src.kyc_pmla_ingest import ingest_kyc as _ingest_fn
        elif law == "pmla":
            from src.kyc_pmla_ingest import ingest_pmla as _ingest_fn
        elif law == "rbi_cyber":
            from src.cyber_ingest import ingest_cyber as _ingest_fn
        elif law == "gdpr":
            from src.gdpr_ingest import ingest_gdpr as _ingest_fn
        elif law == "irdai":
            from src.irdai_ingest import ingest_irdai as _ingest_fn
        else:
            raise ValueError(f"Unknown law code: {law}")

        sections = _ingest_fn()
        law_cfg = banking_config.LAWS[law]
        rq = _get_review_queue(law)
        st = _get_store(law)

        auto_approved = 0
        for section in sections:
            needs_review = section["sensitive"] or section["confidence"] < law_cfg["confidence_threshold"]
            rq.register(section, needs_review)
            if not needs_review:
                st.commit_section(section)
                auto_approved += 1
            audit_log.log_ingestion_classification(
                law_code=law, section_id=section["id"],
                data_classes=section.get("data_classes"),
                required_controls=section.get("required_controls"),
            )

        logger.info(
            "Ingestion complete for %s: %d/%d auto-approved and indexed, %d pending human review.",
            law, auto_approved, len(sections), len(sections) - auto_approved,
        )
        status["error"] = None
        status["done"] = True
    except IngestError as exc:
        logger.exception("Ingestion failed for %s", law)
        status["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - never let a background task die silently
        logger.exception("Ingestion failed for %s", law)
        status["error"] = str(exc)
    finally:
        status["in_progress"] = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    audit_log.init_db()
    run_ingestion()  # DPDP only - other laws are on-demand via POST /ingest/{law}
    yield


app = FastAPI(
    title="India Banking Regulatory Intelligence API (Render build)",
    version="2.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[cfg.ALLOWED_ORIGIN] if cfg.ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": "India Banking Regulatory Intelligence API",
        "docs": "/docs",
        "health": "/health",
        "laws": "/laws",
        "note": "This is the backend API only. The chat UI is a separate frontend deployment.",
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    law: str = Field(default="dpdp", description="One of: dpdp, kyc_aml, pmla, rbi_cyber")

    @field_validator("law")
    @classmethod
    def validate_law(cls, v: str) -> str:
        if v not in banking_config.VALID_LAW_CODES:
            raise ValueError(f"law must be one of {banking_config.VALID_LAW_CODES}")
        return v

    def clean_query(self) -> str:
        cleaned = self.query.strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="Query cannot be empty or whitespace-only.")
        return cleaned


class ReviewDecision(BaseModel):
    section_id: str
    decision: str
    reviewer: str = "demo_reviewer"


class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10_000)


# ---------------------------------------------------------------------------
# Health / observability
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "ingested": _ingestion_done,
        "ingestion_error": _ingestion_error,
        "neo4j_connected": store.ping_neo4j(),
        "sections_indexed": store.indexed_count() if _ingestion_done else 0,
        "other_laws": {
            law: {
                "done": _other_law_status[law]["done"],
                "in_progress": _other_law_status[law]["in_progress"],
                "error": _other_law_status[law]["error"],
                "sections_indexed": _stores[law].indexed_count() if _other_law_status[law]["done"] else 0,
            }
            for law in NEW_LAW_CODES
        },
        "admin_auth_configured": bool(ADMIN_API_KEY),
    }


@app.get("/stats")
def stats(law: str = Query(default="dpdp")):
    if law not in banking_config.VALID_LAW_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown law: {law}")
    rq = _get_review_queue(law)
    st = _get_store(law)
    done, _ = _ingestion_status_for(law)
    counts = rq.stats()
    return {
        "law": law,
        "sections_total": counts["total"],
        "auto_approved": counts["auto_approved"],
        "pending_review": counts["pending_review"],
        "human_approved": counts["approved"],
        "rejected": counts["rejected"],
        "indexed": st.indexed_count() if done else 0,
    }


@app.get("/laws")
def list_laws():
    """Lists all supported laws with pillar + ingestion status, for a
    frontend law-selector."""
    result = {}
    for code, law_cfg in banking_config.LAWS.items():
        done, error = _ingestion_status_for(code)
        in_progress = _other_law_status[code]["in_progress"] if code in NEW_LAW_CODES else False
        result[code] = {
            "label": law_cfg["label"],
            "pillar": law_cfg["pillar"],
            "ingested": done,
            "in_progress": in_progress,
            "ingestion_error": error,
        }
    return result


# ---------------------------------------------------------------------------
# Human-in-the-loop review - GET endpoints stay open (read-only); the
# decision endpoint requires the admin key (fix #3).
# ---------------------------------------------------------------------------
@app.get("/pending-review")
def pending_review(law: str = Query(default="dpdp")):
    if law not in banking_config.VALID_LAW_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown law: {law}")
    return _get_review_queue(law).pending()


@app.get("/section/{section_id}")
def get_section(section_id: str, law: str = Query(default="dpdp")):
    if law not in banking_config.VALID_LAW_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown law: {law}")
    section = _get_review_queue(law).get_section(section_id)
    if not section:
        raise HTTPException(status_code=404, detail=f"No such section {section_id}")
    return {
        "id": section["id"], "title": section["title"], "chapter": section["chapter"],
        "text": section["raw_text"], "source_url": section["source_url"],
        "entities": section["entities"],
    }


@app.post("/approve-review-item", dependencies=[])
def approve_review_item(
    decision: ReviewDecision,
    law: str = Query(default="dpdp"),
    x_admin_key: Optional[str] = Header(default=None),
):
    require_admin(x_admin_key)
    if law not in banking_config.VALID_LAW_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown law: {law}")
    if decision.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")

    rq = _get_review_queue(law)
    st = _get_store(law)
    try:
        entry = rq.decide(decision.section_id, decision.decision, decision.reviewer)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such section {decision.section_id}")

    if decision.decision == "approve":
        st.commit_section(rq.get_section(decision.section_id))

    audit_log.log_review_decision(
        item_type="section", item_reference=decision.section_id, law_code=law,
        decision=entry["status"], reviewer_id=decision.reviewer,
    )

    return {"section_id": decision.section_id, "new_status": entry["status"]}


# ---------------------------------------------------------------------------
# Ingest trigger - DPDP auto-ingests at startup already. All four laws are
# scheduled as a BackgroundTask so the endpoint returns immediately instead
# of blocking for the several minutes an LLM-based ingest can take (fix #2).
# Requires the admin key (fix #3).
# ---------------------------------------------------------------------------
@app.post("/ingest/{law_code}")
def trigger_ingest(
    law_code: str,
    background_tasks: BackgroundTasks,
    x_admin_key: Optional[str] = Header(default=None),
):
    require_admin(x_admin_key)
    if law_code not in banking_config.VALID_LAW_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown law: {law_code}")

    if law_code == "dpdp":
        if _ingestion_done:
            return {"law": "dpdp", "status": "already_done", "indexed": store.indexed_count()}
        background_tasks.add_task(_run_ingestion_for_law_background, "dpdp")
        return {"law": "dpdp", "status": "started"}

    status = _other_law_status[law_code]
    if status["done"]:
        return {"law": law_code, "status": "already_done", "indexed": _get_store(law_code).indexed_count()}
    if status["in_progress"]:
        return {"law": law_code, "status": "already_in_progress"}

    background_tasks.add_task(_run_ingestion_for_law_background, law_code)
    return {
        "law": law_code,
        "status": "started",
        "note": "This runs in the background and may take several minutes for kyc_aml/rbi_cyber "
                "(LLM-based extraction, Groq rate-limited). Poll GET /health or GET /laws for completion.",
    }


# ---------------------------------------------------------------------------
# Policy Engine endpoints (Data Classification -> Masking/Encryption/
# Tokenisation). Pure rule-based, no LLM call - see policy_engine.py.
# ---------------------------------------------------------------------------
@app.post("/classify")
def classify(request: ClassifyRequest):
    classes, keywords = policy_engine.classify_text(request.text)
    return {"data_classes": classes, "matched_keywords": keywords}


@app.post("/evaluate")
def evaluate(request: ClassifyRequest):
    result = policy_engine.evaluate(request.text)
    return {
        "data_classes": result.data_classes,
        "required_controls": result.required_controls,
        "rationale": result.rationale,
        "matched_keywords": result.matched_keywords,
    }


@app.post("/protect")
def protect(request: ClassifyRequest):
    """Runs classify -> policy -> actually APPLIES the recommended controls
    (crypto_utils.apply_controls()) and returns the real before/after output
    - masked text, a Fernet-encrypted token, and a vault-backed surrogate
    token - not just a list of control *names* the way /evaluate does.
    Was built (crypto_utils.py) but never wired to any endpoint until now."""
    result = policy_engine.evaluate(request.text, protect=True)
    return {
        "data_classes": result.data_classes,
        "required_controls": result.required_controls,
        "rationale": result.rationale,
        "matched_keywords": result.matched_keywords,
        "protected": result.protected,
    }


# ---------------------------------------------------------------------------
# Audit & Monitoring export.
# ---------------------------------------------------------------------------
@app.get("/audit-log")
def get_audit_log(law: Optional[str] = Query(default=None), limit: int = Query(default=200, le=2000)):
    return audit_log.export_query_log(law_code=law, limit=limit)


@app.get("/audit-log/reviews")
def get_review_log(limit: int = Query(default=200, le=2000)):
    return audit_log.export_review_log(limit=limit)


@app.get("/audit-log/ingestion")
def get_ingestion_log(law: Optional[str] = Query(default=None), limit: int = Query(default=200, le=2000)):
    return audit_log.export_ingestion_log(law_code=law, limit=limit)


# ---------------------------------------------------------------------------
# Chat - streaming NDJSON, parameterized by `law`. Every terminal state
# (cached, no_answer, pending_review, answered) now logs to the audit
# trail exactly once - see fix #1.
# ---------------------------------------------------------------------------
def query_is_high_risk(query: str, law: str) -> bool:
    lowered = query.lower()
    keywords = banking_config.LAWS[law]["high_risk_query_keywords"]
    return any(kw in lowered for kw in keywords)


def _ndjson(obj: dict) -> str:
    return json.dumps(obj) + "\n"


def _best_context_window(text: str, query_terms: set[str], char_limit: int) -> str:
    """Smart chunking at query time: instead of always taking text[:char_limit]
    (which silently truncates away the actually-relevant part of any
    section longer than char_limit), slide a char_limit-wide window over
    the text and keep the one with the highest density of query-term hits.
    Falls back to the simple prefix when the text already fits or no
    query terms appear anywhere (cheap correctness-preserving default -
    this only changes behavior when it can genuinely do better)."""
    if len(text) <= char_limit or not query_terms:
        return text[:char_limit]

    text_lower = text.lower()
    term_positions: list[int] = []
    for term in query_terms:
        start = 0
        while True:
            idx = text_lower.find(term, start)
            if idx == -1:
                break
            term_positions.append(idx)
            start = idx + len(term)
    if not term_positions:
        return text[:char_limit]

    # Coarse scan in fixed-size steps (not every possible offset - this
    # is a legal-section-length text, a few hundred candidate windows is
    # already far more granularity than the char_limit needs).
    step = max(char_limit // 8, 40)
    best_start, best_hits = 0, -1
    for start in range(0, max(len(text) - char_limit, 0) + 1, step):
        end = start + char_limit
        hits = sum(1 for pos in term_positions if start <= pos < end)
        if hits > best_hits:
            best_start, best_hits = start, hits
    window = text[best_start:best_start + char_limit]
    return ("…" if best_start > 0 else "") + window


def _retrieve(query: str, law: str) -> tuple[str, list[str], list[dict], float, dict[str, float]]:
    law_cfg = banking_config.LAWS[law]
    store_ = _get_store(law)
    query_terms = {t for t in tfidf_search.tokenize(query)}

    t0 = time.perf_counter()
    results = store_.search(query, law_cfg["retrieval_top_k"], score_threshold=law_cfg["hard_cutoff"])
    retrieval_ms = (time.perf_counter() - t0) * 1000

    section_ids = [sid for sid, _ in results]
    t1 = time.perf_counter()
    graph_by_section = store_.graph_context_batch(section_ids)
    graph_ms = (time.perf_counter() - t1) * 1000

    context_str = ""
    citations: list[str] = []
    citation_meta: list[dict] = []
    top_score = 0.0
    for section_id, score in results:
        meta = store_.get_section_meta(section_id)
        if meta is None:
            continue
        top_score = max(top_score, score)
        citations.append(section_id)
        citation_meta.append({
            "id": section_id, "title": meta["title"], "chapter": meta.get("chapter", ""),
            "score": round(score, 3),
        })
        windowed_text = _best_context_window(meta["raw_text"], query_terms, law_cfg["context_char_limit"])
        graph_rows = graph_by_section.get(section_id, [])
        graph_info = "\n".join(f"  - {row['type']}: {row['name']}" for row in graph_rows)
        context_str += f"\n[{section_id}] {windowed_text}\n{graph_info}\n"

    timings = {"retrieval_ms": round(retrieval_ms, 1), "graph_ms": round(graph_ms, 1)}
    logger.info(
        "Retrieval for law=%s query=%r: %d results above HARD_CUTOFF=%.3f, top_score=%.3f, "
        "retrieval_ms=%.1f graph_ms=%.1f",
        law, query, len(results), law_cfg["hard_cutoff"], top_score, retrieval_ms, graph_ms,
    )
    return context_str, citations, citation_meta, top_score, timings


_CITATION_RE = re.compile(r"\[([A-Za-z0-9_:.\-]+)\]")


def _check_citation_faithfulness(answer_text: str, valid_citations: list[str]) -> list[str]:
    """Anti-hallucination guard: scans the generated answer for anything
    that looks like a citation tag (the same "[S<n>]" / "[law:n]" bracket
    style the system prompt asks the model to use) and returns any that
    do NOT correspond to a section actually retrieved and placed in
    context. The model is instructed to cite only from context, but a
    system prompt is a request, not a guarantee - this is a cheap,
    deterministic, post-hoc check that catches the model inventing a
    citation number that was never in front of it. Doesn't require a
    second LLM call, so it adds no latency or generation cost."""
    valid = set(valid_citations)
    mentioned = set(_CITATION_RE.findall(answer_text))
    return sorted(mentioned - valid)


def _stream_answer(query: str, law: str):
    t_request_start = time.perf_counter()
    law_cfg = banking_config.LAWS[law]

    # Ingestion is no longer triggered inline here for the LLM-based laws -
    # that was the old blocking bug. If a law hasn't been ingested yet, we
    # tell the caller to POST /ingest/{law} first rather than silently
    # kicking off a multi-minute job mid-stream.
    done, error = _ingestion_status_for(law)
    if law == "dpdp" and not done and error is None:
        run_ingestion()
        done, error = _ingestion_status_for(law)

    if not done and error is None:
        yield _ndjson({
            "type": "done", "status": "no_answer",
            "note": f"{law_cfg['label']} has not been ingested yet. "
                    f"POST /ingest/{law} first, then retry this question.",
            "citations": [], "citation_meta": [],
        })
        return

    if error:
        yield _ndjson({
            "type": "done", "status": "no_answer",
            "note": f"The source document for {law_cfg['label']} could not be loaded ({error}). "
                    f"POST /ingest/{law} to retry.",
            "citations": [], "citation_meta": [],
        })
        return

    cache_key = f"{law}:{query}"
    cached = answer_cache.get(cache_key)
    if cached:
        yield _ndjson({"type": "token", "text": cached["answer"]})
        yield _ndjson({
            "type": "done", "status": "answered",
            "citations": cached["citations"], "citation_meta": cached["citation_meta"], "cached": True,
            "timings": {"total_ms": round((time.perf_counter() - t_request_start) * 1000, 1), "cache_hit": True},
        })
        audit_log.log_query(audit_log.QueryLogEntry(
            law_code=law, query_text=query, retrieved_section_ids=cached["citations"],
            answer_text=cached["answer"], was_high_risk=False, required_human_review=False,
            data_classes=[], required_controls=[],
        ))
        return

    policy_result = policy_engine.evaluate(query)

    context_str, citations, citation_meta, top_score, retrieve_timings = _retrieve(query, law)

    if not citations:
        store_ = _get_store(law)
        if store_.indexed_count() == 0:
            note = "Still finishing ingestion of this source document - please try again in a moment."
        else:
            note = law_cfg["not_found_note"]
        yield _ndjson({
            "type": "done", "status": "no_answer", "note": note,
            "citations": [], "citation_meta": [],
            "timings": {**retrieve_timings, "total_ms": round((time.perf_counter() - t_request_start) * 1000, 1)},
        })
        audit_log.log_query(audit_log.QueryLogEntry(
            law_code=law, query_text=query, retrieved_section_ids=[],
            answer_text=None, was_high_risk=False, required_human_review=False,
            data_classes=policy_result.data_classes, required_controls=policy_result.required_controls,
        ))
        return

    high_risk = query_is_high_risk(query, law)
    if high_risk:
        yield _ndjson({
            "type": "done", "status": "pending_review",
            "note": f"High-risk question for {law_cfg['label']}. Held for human review instead "
                    f"of an auto-generated answer.",
            "citations": citations, "citation_meta": citation_meta,
            "timings": {**retrieve_timings, "total_ms": round((time.perf_counter() - t_request_start) * 1000, 1)},
        })
        audit_log.log_query(audit_log.QueryLogEntry(
            law_code=law, query_text=query, retrieved_section_ids=citations,
            answer_text=None, was_high_risk=True, required_human_review=True,
            data_classes=policy_result.data_classes, required_controls=policy_result.required_controls,
        ))
        return

    low_confidence = top_score < law_cfg["similarity_threshold"]
    user_prompt = f"Context from {law_cfg['label']}:\n{context_str}\n\nQuestion: {query}"

    full_answer = ""
    t_gen_start = time.perf_counter()
    try:
        for piece in stream_chat([
            {"role": "system", "content": law_cfg["system_prompt"]},
            {"role": "user", "content": user_prompt},
        ]):
            full_answer += piece
            yield _ndjson({"type": "token", "text": piece})
    except GroqError as exc:
        logger.exception("Groq generation failed")
        yield _ndjson({"type": "error", "detail": str(exc)})
        audit_log.log_query(audit_log.QueryLogEntry(
            law_code=law, query_text=query, retrieved_section_ids=citations,
            answer_text=None, was_high_risk=False, required_human_review=False,
            data_classes=policy_result.data_classes, required_controls=policy_result.required_controls,
        ))
        return
    generation_ms = round((time.perf_counter() - t_gen_start) * 1000, 1)

    # Anti-hallucination guard: catch the model citing a section number
    # that was never actually retrieved/placed in its context. The system
    # prompt tells it to cite only from context, but that's an
    # instruction, not an enforcement mechanism - this is the enforcement.
    hallucinated = _check_citation_faithfulness(full_answer, citations)
    if hallucinated:
        logger.warning(
            "Citation faithfulness check failed for law=%s query=%r: model cited %s, "
            "which were not in the retrieved context %s",
            law, query, hallucinated, citations,
        )
        disclaimer = (
            f"\n\n(Note: this answer referenced {', '.join(hallucinated)}, which weren't in the "
            f"retrieved context - treat that part with extra caution and verify independently.)"
        )
        full_answer += disclaimer
        yield _ndjson({"type": "token", "text": disclaimer})

    if low_confidence:
        disclaimer = "\n\n(Low-confidence match - please verify against the cited section text.)"
        full_answer += disclaimer
        yield _ndjson({"type": "token", "text": disclaimer})

    answer_cache.set(cache_key, {
        "answer": full_answer, "citations": citations, "citation_meta": citation_meta,
    })
    total_ms = round((time.perf_counter() - t_request_start) * 1000, 1)
    yield _ndjson({
        "type": "done", "status": "answered",
        "citations": citations, "citation_meta": citation_meta, "cached": False,
        "timings": {**retrieve_timings, "generation_ms": generation_ms, "total_ms": total_ms},
        "hallucinated_citations": hallucinated,
    })
    logger.info(
        "Answered law=%s query=%r in total_ms=%.1f (retrieval_ms=%.1f graph_ms=%.1f generation_ms=%.1f)",
        law, query, total_ms, retrieve_timings["retrieval_ms"], retrieve_timings["graph_ms"], generation_ms,
    )
    audit_log.log_query(audit_log.QueryLogEntry(
        law_code=law, query_text=query, retrieved_section_ids=citations,
        answer_text=full_answer, was_high_risk=False, required_human_review=False,
        data_classes=policy_result.data_classes, required_controls=policy_result.required_controls,
    ))


def _client_key(http_request: Request) -> str:
    """Fix #4: prefer X-Forwarded-For (set by Render's proxy) over the
    directly-connected socket address, which is always the proxy's own
    IP in that environment and would otherwise rate-limit everyone as
    one client."""
    forwarded = http_request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return http_request.client.host if http_request.client else "unknown"


@app.post("/ask")
def ask_dpdp(request: QueryRequest, http_request: Request):
    client_key = _client_key(http_request)
    if not rate_limiter.allow(client_key):
        raise HTTPException(status_code=429, detail="Too many requests - please slow down.")
    query = request.clean_query()
    return StreamingResponse(_stream_answer(query, request.law), media_type="application/x-ndjson")
