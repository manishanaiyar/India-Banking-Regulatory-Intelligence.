"""
DPDP Act + Banking Regulatory Intelligence GraphRAG service - Render/Groq/AuraDB build.

Extended from the original DPDP-only main.py to support four laws:
dpdp, kyc_aml, pmla, rbi_cyber - each with its OWN KnowledgeStore and
ReviewQueue instance (see `_stores` / `_review_queues` below), so DPDP's
behavior is completely unaffected by the new laws: same TF-IDF index,
same Neo4j writes, same review queue object as before. New laws are
additive, not a rewrite of the DPDP path.

Startup only auto-ingests DPDP, exactly as before (see run_ingestion(),
unchanged). KYC/PMLA/Cyber are ingested on-demand via
POST /ingest/{law_code} - fetching three more government PDFs on every
cold start would slow down an already-slow free-tier wake-up further, so
this is opt-in rather than automatic. Trigger it once after deploy, or
wire it into your own startup script if you want it automatic.

The frontend is a SEPARATE deployment (Vercel) - this process serves only
the JSON/NDJSON API, no static files. (Unchanged from the original.)
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src import dpdp_config as cfg
from src.dpdp_ingest import build_tagged_sections, fetch_act_pdf
from src.dpdp_stores import AnswerCache, KnowledgeStore, RateLimiter, ReviewQueue
from src.groq_client import GroqError, stream_chat

from src import banking_config
from src import policy_engine
from src import audit_log
from src.ingest_common import IngestError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dpdp.main")

# ---------------------------------------------------------------------------
# DPDP - exactly as in your original main.py. Nothing below this block is
# changed for the DPDP law; new laws get their own separate instances
# further down instead of sharing (or replacing) these.
# ---------------------------------------------------------------------------
store = KnowledgeStore()
review_queue = ReviewQueue()
answer_cache = AnswerCache()
rate_limiter = RateLimiter(cfg.RATE_LIMIT_REQUESTS_PER_MINUTE)

_ingestion_done = False
_ingestion_error: Optional[str] = None


def run_ingestion() -> None:
    """Unchanged from your original. Idempotent DPDP ingestion."""
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

        logger.info(
            "Ingestion complete: %d/%d auto-approved and indexed, %d pending human review.",
            auto_approved, len(sections), len(sections) - auto_approved,
        )
        _ingestion_error = None
        _ingestion_done = True
    except Exception as exc:
        logger.exception("Ingestion failed")
        _ingestion_error = str(exc)


# ---------------------------------------------------------------------------
# NEW: KYC/AML, PMLA, RBI Cyber - each gets its own store + review queue,
# created LAZILY (only when that law's ingestion is actually triggered via
# POST /ingest/{law_code}), not eagerly at module import time.
#
# Why lazy: KnowledgeStore() opens a real Neo4j driver connection (see
# dpdp_stores.py: `GraphDatabase.driver(NEO4J_URI, ...)` runs in
# __init__). Creating all three eagerly at startup would open 3 extra
# driver connections to your AuraDB Free instance before anyone has even
# asked to use those laws - AuraDB Free tiers typically cap concurrent
# connections tightly, and this could compete with or crowd out the DPDP
# store's own connection. Lazy creation means a fresh deploy that only
# ever gets DPDP traffic never opens more than the one Neo4j connection
# your original app already used.
#
# All instances still point at the SAME AuraDB database (env vars are
# global, read once by dpdp_config.py) - that's why ingest_common.py
# namespaces every new-law section id as "<law_code>:<section_number>"
# (e.g. "kyc_aml:4.3"). Verified against your real dpdp_ingest.py: DPDP's
# own ids are "S1".."S44", so there's no collision either way, but the
# namespacing stays as a deliberate safety margin for the new laws.
# ---------------------------------------------------------------------------
NEW_LAW_CODES = ("kyc_aml", "pmla", "rbi_cyber")

_stores: dict[str, KnowledgeStore] = {"dpdp": store}
_review_queues: dict[str, ReviewQueue] = {"dpdp": review_queue}
_other_law_status: dict[str, dict] = {
    law: {"done": False, "error": None} for law in NEW_LAW_CODES
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


def run_ingestion_for_law(law: str) -> dict:
    """Ingest one of the three new laws on demand. Returns a summary dict.
    Raises IngestError (surfaced as HTTP 502 by the endpoint below) if the
    source document can't be fetched or parsed - same fail-loud philosophy
    as run_ingestion() above."""
    if law == "dpdp":
        run_ingestion()
        return {
            "law": "dpdp", "done": _ingestion_done, "error": _ingestion_error,
            "indexed": store.indexed_count() if _ingestion_done else 0,
        }

    if law not in NEW_LAW_CODES:
        raise ValueError(f"Unknown law code: {law}")

    status = _other_law_status[law]
    if status["done"]:
        st = _get_store(law)
        return {"law": law, "done": True, "error": None, "indexed": st.indexed_count()}

    # Imported here (not at module top) so the app can still start even
    # before `pypdf` is added to requirements.txt - only /ingest/{law}
    # calls need it, not app startup.
    if law == "kyc_aml":
        from src.kyc_pmla_ingest import ingest_kyc as _ingest_fn
    elif law == "pmla":
        from src.kyc_pmla_ingest import ingest_pmla as _ingest_fn
    elif law == "rbi_cyber":
        from src.cyber_ingest import ingest_cyber as _ingest_fn

    try:
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

        logger.info(
            "Ingestion complete for %s: %d/%d auto-approved and indexed, %d pending human review.",
            law, auto_approved, len(sections), len(sections) - auto_approved,
        )
        status["error"] = None
        status["done"] = True
        return {
            "law": law, "done": True, "error": None,
            "sections_parsed": len(sections), "auto_approved": auto_approved,
            "pending_review": len(sections) - auto_approved,
        }
    except IngestError as exc:
        logger.exception("Ingestion failed for %s", law)
        status["error"] = str(exc)
        raise
    except Exception as exc:  # noqa: BLE001 - wrap unexpected errors as IngestError too
        logger.exception("Ingestion failed for %s", law)
        status["error"] = str(exc)
        raise IngestError(str(exc)) from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_ingestion()  # DPDP only, exactly as before - other laws are on-demand
    audit_log.init_db()
    yield


app = FastAPI(
    title="India Banking Regulatory Intelligence API (Render build)",
    version="2.0.0",
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
# Health / observability - DPDP fields unchanged (same keys, same values,
# same behavior as your original); new "laws" block added alongside.
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
                "error": _other_law_status[law]["error"],
                "sections_indexed": _stores[law].indexed_count() if _other_law_status[law]["done"] else 0,
            }
            for law in NEW_LAW_CODES
        },
    }


@app.get("/stats")
def stats(law: str = Query(default="dpdp")):
    """Unchanged for law="dpdp" (the default, matching your original
    no-argument call). Pass ?law=kyc_aml etc. to get stats for a new law."""
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
    """New: list all supported laws with their pillar and current
    ingestion status, for a frontend law-selector."""
    result = {}
    for code, law_cfg in banking_config.LAWS.items():
        done, error = _ingestion_status_for(code)
        result[code] = {
            "label": law_cfg["label"],
            "pillar": law_cfg["pillar"],
            "ingested": done,
            "ingestion_error": error,
        }
    return result


# ---------------------------------------------------------------------------
# Human-in-the-loop review - same endpoints, now accept ?law=... (default
# "dpdp" preserves exact original behavior for any existing client that
# doesn't send the parameter at all).
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


@app.post("/approve-review-item")
def approve_review_item(decision: ReviewDecision, law: str = Query(default="dpdp")):
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
        decision=entry["status"], reviewer_note=decision.reviewer,
    )

    return {"section_id": decision.section_id, "new_status": entry["status"]}


# ---------------------------------------------------------------------------
# NEW: ingest trigger for the three new laws (DPDP already auto-ingests at
# startup, same as before). Protect this behind whatever admin-auth your
# deployment uses for /approve-review-item, if any - this router does not
# add its own auth, to avoid guessing at a pattern you haven't shown me.
# ---------------------------------------------------------------------------
@app.post("/ingest/{law_code}")
def trigger_ingest(law_code: str):
    if law_code not in banking_config.VALID_LAW_CODES:
        raise HTTPException(status_code=400, detail=f"Unknown law: {law_code}")
    try:
        return run_ingestion_for_law(law_code)
    except IngestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# NEW: Policy Engine endpoints (Data Classification -> Masking/Encryption/
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


# ---------------------------------------------------------------------------
# NEW: Audit & Monitoring export.
# ---------------------------------------------------------------------------
@app.get("/audit-log")
def get_audit_log(law: Optional[str] = Query(default=None), limit: int = Query(default=200, le=2000)):
    return audit_log.export_query_log(law_code=law, limit=limit)


@app.get("/audit-log/reviews")
def get_review_log(limit: int = Query(default=200, le=2000)):
    return audit_log.export_review_log(limit=limit)


# ---------------------------------------------------------------------------
# Chat - same streaming NDJSON contract as your original /ask, now
# parameterized by `law`. Every code path (cache hit, no citations,
# high-risk, low-confidence, normal answer) is preserved exactly; the
# only additions are (a) looking up config/store/queue by law instead of
# using the DPDP globals directly, and (b) an audit_log.log_query() call
# at each of the three terminal states.
# ---------------------------------------------------------------------------
def query_is_high_risk(query: str, law: str) -> bool:
    lowered = query.lower()
    keywords = banking_config.LAWS[law]["high_risk_query_keywords"]
    return any(kw in lowered for kw in keywords)


def _ndjson(obj: dict) -> str:
    return json.dumps(obj) + "\n"


def _retrieve(query: str, law: str) -> tuple[str, list[str], list[dict], float]:
    """Same logic as your original _retrieve(), parameterized by law:
    looks up the right store and the right retrieval thresholds instead
    of the DPDP-only globals."""
    law_cfg = banking_config.LAWS[law]
    store_ = _get_store(law)
    results = store_.search(query, law_cfg["retrieval_top_k"], score_threshold=law_cfg["hard_cutoff"])

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
        graph_rows = store_.graph_context(section_id)
        graph_info = "\n".join(f"  - {row['type']}: {row['name']}" for row in graph_rows)
        context_str += f"\n[{section_id}] {meta['raw_text'][:law_cfg['context_char_limit']]}\n{graph_info}\n"

    logger.info(
        "Retrieval for law=%s query=%r: %d results above HARD_CUTOFF=%.3f, top_score=%.3f",
        law, query, len(results), law_cfg["hard_cutoff"], top_score,
    )
    return context_str, citations, citation_meta, top_score


def _stream_answer(query: str, law: str):
    """Same generator contract as your original _stream_answer(), plus a
    call to audit_log.log_query() at every terminal `done` state, and a
    policy_engine.evaluate() call on the query text so the audit record
    captures what data classification / controls the QUESTION itself
    touches (useful signal for spotting risky query patterns over time -
    separate from classifying the answer content, which you may want to
    add later once you decide what "answer content classification" should
    mean for your compliance program)."""
    law_cfg = banking_config.LAWS[law]

    if law == "dpdp":
        run_ingestion()
    else:
        run_ingestion_for_law(law)  # will raise IngestError to the caller if never ingested + fetch fails

    done, error = _ingestion_status_for(law)
    if error:
        yield _ndjson({
            "type": "done", "status": "no_answer",
            "note": f"The source document for {law_cfg['label']} could not be loaded ({error}). "
                    f"This will retry automatically on the next question.",
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
        })
        return

    policy_result = policy_engine.evaluate(query)

    context_str, citations, citation_meta, top_score = _retrieve(query, law)

    if not citations:
        store_ = _get_store(law)
        if store_.indexed_count() == 0:
            note = "Still finishing ingestion of this source document - please try again in a moment."
        else:
            note = law_cfg["not_found_note"]
        yield _ndjson({
            "type": "done", "status": "no_answer", "note": note,
            "citations": [], "citation_meta": [],
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
        return

    if low_confidence:
        disclaimer = "\n\n(Low-confidence match - please verify against the cited section text.)"
        full_answer += disclaimer
        yield _ndjson({"type": "token", "text": disclaimer})

    answer_cache.set(cache_key, {
        "answer": full_answer, "citations": citations, "citation_meta": citation_meta,
    })
    yield _ndjson({
        "type": "done", "status": "answered",
        "citations": citations, "citation_meta": citation_meta, "cached": False,
    })
    audit_log.log_query(audit_log.QueryLogEntry(
        law_code=law, query_text=query, retrieved_section_ids=citations,
        answer_text=full_answer, was_high_risk=False, required_human_review=False,
        data_classes=policy_result.data_classes, required_controls=policy_result.required_controls,
    ))


@app.post("/ask")
def ask_dpdp(request: QueryRequest, http_request: Request):
    client_key = http_request.client.host if http_request.client else "unknown"
    if not rate_limiter.allow(client_key):
        raise HTTPException(status_code=429, detail="Too many requests - please slow down.")
    query = request.clean_query()
    return StreamingResponse(_stream_answer(query, request.law), media_type="application/x-ndjson")
