# India DPDP Act - GraphRAG Assistant with Human-in-the-Loop Review

A working proof of concept for a policy knowledge-graph and retrieval-augmented assistant, scoped to
a single law - India's **Digital Personal Data Protection Act, 2023** - so the full pipeline is easy
to run, explain, and verify end to end. Runs entirely in Google Colab. Fully open source. No API key
needed anywhere.

## What this is

A demo of: a **knowledge graph** connecting a law to its obligations, rights, and penalties; a
**vector store** for semantic search over the legal text; a **local, open-source LLM** that retrieves
and generates cited answers; a **human-in-the-loop review gate** so nothing unverified reaches an end
user; and a chat interface for people to actually use it - packaged as a FastAPI backend + Streamlit
frontend, exposed publicly via a free tunnel.

## Demo

▶️ [Watch the demo recording](https://raw.githubusercontent.com/manishanaiyar/India-DPDP-Act---GraphRAG-Assistant-with-Human-in-the-Loop-Review/227a249d598aa6d8109b200c53b8f735e63a9091/20260816-0911-03.4433891%20(1).mp4)
(opens/plays directly in your browser)

## Run it

1. Open `DPDP_Act_FastAPI_Streamlit_Demo.ipynb` in [Google Colab](https://colab.research.google.com/).
2. Runtime -> Run all.
3. Cells 2-3 install and start Neo4j and Ollama (a few minutes, one-time per session).
4. Cell 8 waits for the backend and runs a live test in-notebook.
5. Cell 9 prints a public HTTPS URL (`*.trycloudflare.com`) - open it directly, no signup or
   password needed.

No local setup, no API keys, no paid services.

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
 Written to Qdrant (vectors)         Sits in a review queue until a human
 + Neo4j (graph)                     approves/rejects it via the Streamlit
  |                                  sidebar - only then does it get written
  |                                  to Qdrant + Neo4j too
  v
FastAPI backend (/ask, /pending-review, /approve-review-item)
  |
  v
On a question: vector search (Qdrant) + graph context (Neo4j) retrieved,
then Ollama (qwen2.5:3b, local, no API key) generates a cited answer -
UNLESS the question itself is high-risk (penalty/obligation/cross-border),
in which case it's also held for human review instead of answered directly
  |
  v
Streamlit chat UI  --->  Cloudflare quick tunnel  --->  public HTTPS URL
```

## Tech stack

All open source, all free, no signup required anywhere.

| Component | Tool | Role |
|---|---|---|
| Knowledge graph | Neo4j (Community) | Sections linked to obligations, rights, penalties, definitions |
| Vector store | Qdrant (in-memory) | Semantic search over section text |
| Embeddings | `sentence-transformers` (`all-MiniLM-L6-v2`) | Turns section text and questions into vectors |
| Local LLM | Ollama, running `qwen2.5:3b` | Generates the final answer text - only called per question |
| Backend | FastAPI + Uvicorn | REST API: `/ask`, `/pending-review`, `/approve-review-item`, `/health` |
| Frontend | Streamlit | Chat interface + live human-review sidebar |
| Public URL | `cloudflared` quick tunnel | Free, no signup, no interstitial warning page |
| Source data | Official Gazette PDF, [MeitY](https://www.meity.gov.in/) | Fetched live at runtime, not hardcoded |

## Human-in-the-loop: two checkpoints

**Checkpoint 1 - before anything enters the graph/vector store.** Every parsed section is tagged by
chapter and title. Any section touching an Obligation or Penalty, or with a low parse-confidence
score, is held out of Qdrant/Neo4j until a human approves it through the Streamlit sidebar.

**Checkpoint 2 - before a generated answer is shown.** Questions containing high-risk keywords
(penalty, obligation, breach, cross-border transfer) never get an auto-generated answer - they come
back `pending_review` with the retrieved context attached, for a human to check before anything
resembling advice reaches an end user.

### Why this matters - an example observed while testing this notebook

Asking the one-word query **"rules"** correctly retrieved Section 41 (citations are computed
independently of the generated text, via vector search), but the local model's generated sentence
mislabeled it as "Section 42" while paraphrasing. Asking the more specific **"what does section 41
require"** produced an accurate answer citing the same section correctly.

This is a real, observed limitation of small local LLMs: they can retrieve the right source and still
misstate a detail while summarizing it - which is exactly why citations are shown separately from the
generated prose, and why high-risk answers are gated behind human review instead of trusted
automatically.

## Performance notes

Generation runs on Ollama's CPU inference by default on Colab's free tier, which is the main source
of per-question latency. Three things help:
- **Switch to a GPU runtime** (Runtime -> Change runtime type -> T4 GPU) before running - Ollama
  auto-detects CUDA, typically a 5-10x speedup, zero code changes needed. Do this first.
- The model is **pre-warmed** right after it's pulled (Section 3 of the notebook), so the first real
  question isn't also paying the one-time cost of loading weights into memory.
- Generation is capped (`num_predict`) and the model is kept loaded between calls (`keep_alive`), and
  identical repeat questions are served from an in-memory cache instead of re-running generation.

Even optimized, `qwen2.5:3b` is a small model - expect a few seconds to ~15-20s per question on GPU,
30-90s+ on CPU. Swapping to `qwen2.5:1.5b` would trade some answer precision for more speed if needed.

## Known limitations

- **Tagging is rule-based** (chapter/title keyword matching), not LLM-based, for reliability and
  speed in a live demo - see "Next steps" below for how a real extraction agent would replace this.
- **Only the original 2023 Act text is covered** - the Digital Personal Data Protection Rules, 2025
  (notified 13 November 2025) are not included.
- **Neo4j and Qdrant are ephemeral** - both live inside the Colab session and are wiped when it ends.
- **The local LLM can misstate details while summarizing**, even when citing the correct source - see
  the example above.

## Next steps for a production system

1. Replace rule-based tagging with a real LLM extraction agent run as an offline batch job (not
   blocking server startup) - the review-queue mechanics here plug in directly, only the source of
   the confidence score changes.
2. Persist Neo4j and Qdrant outside the Colab container.
3. Also ingest the Digital Personal Data Protection Rules, 2025.
4. Add amendment monitoring: stage detected law changes for human confirmation before they update
   the graph.
5. Repeat this same pattern per country to build out a full multi-jurisdiction policy graph
   (GDPR, CCPA, LGPD, PIPL, and 70+ other jurisdictions).
6. Swap `qwen2.5:3b` for a larger Ollama model (e.g. `qwen2.5:7b`, `llama3.1:8b`) if answer precision
   needs to improve.

## Source

Official Gazette of India, Ministry of Law and Justice, 11 August 2023, published by the Ministry of
Electronics and Information Technology (MeitY):
https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf

## License

This project is for educational/demonstration purposes. The DPDP Act text is a government work; this
repository's code is provided as-is for learning and research.
