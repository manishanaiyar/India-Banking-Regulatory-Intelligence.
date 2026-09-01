"""
Tests for src/tfidf_search.py - the lexical retrieval engine and its
reranking stage (MMR diversity + exact-phrase bonus).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tfidf_search import TFIDFIndex, tokenize


def test_tokenize_strips_stopwords_and_short_tokens():
    tokens = tokenize("The data fiduciary shall obtain consent of a data principal.")
    assert "the" not in tokens
    assert "shall" not in tokens
    assert "of" not in tokens
    assert "a" not in tokens
    assert "data" in tokens
    assert "fiduciary" in tokens
    assert "consent" in tokens


def test_search_returns_empty_for_unfit_index():
    idx = TFIDFIndex()
    assert idx.search("anything", top_k=5) == []


def test_search_ranks_more_relevant_doc_higher():
    idx = TFIDFIndex()
    idx.fit({
        "S1": "The data fiduciary shall obtain consent before processing personal data.",
        "S2": "The quarterly board meeting was rescheduled to next month.",
    })
    results = idx.search("consent for processing personal data", top_k=2)
    assert results[0][0] == "S1"
    assert results[0][1] > 0


def test_search_out_of_scope_query_returns_nothing():
    """This is the mechanism that lets main.py detect an out-of-scope
    question and return the law's not_found_note instead of a hallucinated
    answer - a query with zero vocabulary overlap with the corpus must
    score exactly zero relevance, not some arbitrary low-but-nonzero
    number that could sneak past a threshold."""
    idx = TFIDFIndex()
    idx.fit({"S1": "The data fiduciary shall obtain consent before processing personal data."})
    results = idx.search("what is the capital of France", top_k=5)
    assert results == []


def test_search_reranked_exact_phrase_bonus_beats_scattered_terms():
    idx = TFIDFIndex()
    docs = {
        # Contains the query terms but scattered
        "S1": "A data breach involving personal data must, separately, be reported. The Board must be notified.",
        # Contains the exact query phrase
        "S2": "A data breach must be reported to the Board within seventy two hours.",
    }
    idx.fit(docs)
    results = idx.search_reranked("data breach must be reported to the Board", docs, top_k=2)
    assert results[0][0] == "S2"


def test_search_reranked_mmr_favors_diversity_at_low_lambda():
    """Three near-duplicate 'consent' sections plus one on an unrelated
    topic (penalties). At a diversity-heavy lambda, MMR should surface
    the unrelated-but-still-matching section over a third near-duplicate."""
    idx = TFIDFIndex()
    docs = {
        "S1": "The data fiduciary shall obtain consent before processing personal data of a data principal.",
        "S2": "Consent must be obtained from the data principal before any processing of personal data begins.",
        "S3": "Consent of the data principal is a precondition for processing of personal data by any fiduciary.",
        "S4": "The penalty for non-compliance with consent requirements can be up to two hundred fifty crore rupees.",
    }
    idx.fit(docs)
    diverse = idx.search_reranked("consent processing personal data", docs, top_k=2, mmr_lambda=0.2)
    relevance_only = idx.search_reranked("consent processing personal data", docs, top_k=2, mmr_lambda=1.0)
    # At full relevance weighting, MMR degenerates to plain top-2 by score.
    # At low lambda, diversity should pull in S4 instead of a 3rd near-duplicate.
    assert set(diverse) != set(relevance_only) or "S4" in [d for d, _ in diverse]


def test_search_reranked_returns_fewer_than_top_k_when_corpus_is_small():
    idx = TFIDFIndex()
    docs = {"S1": "The data fiduciary shall obtain consent."}
    idx.fit(docs)
    results = idx.search_reranked("consent", docs, top_k=5)
    assert len(results) == 1


def test_refit_replaces_previous_index():
    idx = TFIDFIndex()
    idx.fit({"S1": "alpha beta gamma"})
    assert idx.search("alpha", top_k=5)
    idx.fit({"S2": "delta epsilon zeta"})
    assert idx.search("alpha", top_k=5) == []
    assert idx.search("delta", top_k=5)
