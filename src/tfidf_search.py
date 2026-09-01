"""
A small, dependency-free TF-IDF search index.

This exists specifically because Render's free web service caps RAM at
512MB - sentence-transformers alone (with its torch dependency) generally
won't fit comfortably alongside everything else in that budget. TF-IDF is a
classic, well-understood lexical (keyword-overlap) retrieval method: no
model weights, no torch, just term-frequency math over Python dicts.

The real trade-off (documented in dpdp_config.py next to the threshold
constants) is that this is lexical, not semantic: it matches on shared
vocabulary between the query and a section, not on meaning. It's a
deliberate, honest swap of retrieval quality for RAM headroom.
"""

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "in", "and", "or", "is", "are", "shall",
    "may", "be", "as", "by", "for", "on", "with", "this", "that", "any",
    "such", "under", "not", "if", "has", "have", "been", "will", "which",
    "who", "whom", "it", "its", "at", "from", "into", "than", "so", "but",
    "no", "nor", "was", "were", "do", "does", "did", "can", "could",
    "would", "should", "shall not",
})


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class TFIDFIndex:
    """Fit on a small corpus (here: <=44 DPDP Act sections) and re-fit
    cheaply whenever the set of approved/indexed sections changes - at this
    corpus size, a full rebuild is a few milliseconds, so there's no need
    for incremental updates."""

    def __init__(self) -> None:
        self._doc_ids: list[str] = []
        self._doc_vectors: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}

    def fit(self, documents: dict[str, str]) -> None:
        """documents: {doc_id: text}. Replaces any previously fitted index."""
        self._doc_ids = list(documents.keys())
        tokenized = [tokenize(documents[doc_id]) for doc_id in self._doc_ids]

        n_docs = len(tokenized)
        doc_freq: Counter[str] = Counter()
        for tokens in tokenized:
            doc_freq.update(set(tokens))

        # Smoothed IDF (like sklearn's default): avoids divide-by-zero and
        # keeps terms that appear in every document from collapsing to 0.
        self._idf = {
            term: math.log((1 + n_docs) / (1 + df)) + 1.0
            for term, df in doc_freq.items()
        }

        self._doc_vectors = []
        for tokens in tokenized:
            self._doc_vectors.append(self._vectorize(tokens))

    def _vectorize(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        vec: dict[str, float] = {}
        for term, count in tf.items():
            idf = self._idf.get(term)
            if idf is None:
                continue  # term never seen in the fitted corpus - contributes nothing
            # Sublinear TF scaling (1 + log(count)) so a section repeating a
            # word 10 times isn't weighted 10x a section mentioning it once.
            vec[term] = (1.0 + math.log(count)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {k: v / norm for k, v in vec.items()}
        return vec

    def search(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """Returns [(doc_id, cosine_similarity), ...] sorted descending,
        length <= top_k. An empty index or a query with zero corpus-vocab
        overlap correctly returns [] / all-zero scores - this is what lets
        the groundedness check in main.py detect out-of-scope questions."""
        if not self._doc_ids:
            return []
        query_vec = self._vectorize(tokenize(query))
        if not query_vec:
            return []

        scores: list[tuple[str, float]] = []
        for doc_id, doc_vec in zip(self._doc_ids, self._doc_vectors):
            # Cosine similarity via dot product (both vectors are already
            # unit-normalized in _vectorize, so this IS the cosine similarity).
            shared_terms = query_vec.keys() & doc_vec.keys()
            score = sum(query_vec[t] * doc_vec[t] for t in shared_terms)
            if score > 0:
                scores.append((doc_id, score))

        scores.sort(key=lambda pair: pair[1], reverse=True)
        return scores[:top_k]

    def search_reranked(
        self, query: str, texts: dict[str, str], top_k: int,
        candidate_pool: int = 15, mmr_lambda: float = 0.7, phrase_bonus: float = 0.15,
    ) -> list[tuple[str, float]]:
        """Two-stage retrieval: (1) TF-IDF cosine similarity picks a wider
        candidate pool, (2) a cheap reranking pass reorders that pool
        before truncating to top_k:

          - Exact-phrase bonus: a section containing the literal query
            string (case-insensitive) gets a flat score bump - cosine
            similarity alone can rank a section that scatters the same
            words across unrelated sentences above one that states the
            exact phrase the user asked about.
          - MMR (Maximal Marginal Relevance) diversity selection: greedily
            picks each next result to maximize
            `mmr_lambda * relevance - (1 - mmr_lambda) * max_similarity_to_already_picked`,
            so a small top_k doesn't fill up with several near-duplicate
            sections that all happen to share the same top terms, at the
            expense of a genuinely different but still relevant section.

        `texts` must map doc_id -> raw text (for the phrase-bonus check
        and for computing pairwise similarity during MMR - both need the
        already-fitted doc vectors, which _vectorize() recomputes cheaply
        at this corpus size rather than caching a second copy).
        """
        candidates = self.search(query, candidate_pool)
        if not candidates:
            return []

        query_lower = query.strip().lower()
        boosted: list[tuple[str, float]] = []
        for doc_id, score in candidates:
            bonus = phrase_bonus if query_lower and query_lower in texts.get(doc_id, "").lower() else 0.0
            boosted.append((doc_id, score + bonus))
        boosted.sort(key=lambda pair: pair[1], reverse=True)

        if len(boosted) <= top_k:
            return boosted

        doc_vec_by_id = {
            doc_id: self._doc_vectors[self._doc_ids.index(doc_id)]
            for doc_id, _ in boosted
        }

        def cosine(a: dict[str, float], b: dict[str, float]) -> float:
            shared = a.keys() & b.keys()
            return sum(a[t] * b[t] for t in shared)

        remaining = list(boosted)
        selected: list[tuple[str, float]] = []
        while remaining and len(selected) < top_k:
            best_idx, best_mmr = 0, float("-inf")
            for i, (doc_id, relevance) in enumerate(remaining):
                if selected:
                    max_sim = max(cosine(doc_vec_by_id[doc_id], doc_vec_by_id[s_id]) for s_id, _ in selected)
                else:
                    max_sim = 0.0
                mmr_score = mmr_lambda * relevance - (1 - mmr_lambda) * max_sim
                if mmr_score > best_mmr:
                    best_idx, best_mmr = i, mmr_score
            selected.append(remaining.pop(best_idx))
        return selected
