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
