# BRACU QUERTEX — FastAPI + pgvector

RAG chatbot over `BRACU_RAG_Corpus/` (199 docs, 1,041 chunks). FastAPI on Vercel, Neon Postgres + pgvector. See README.md.

## Commands
- Setup: `pip install -r requirements-dev.txt`, `python scripts/fetch_model.py`
- Build/refresh the index in Neon: `python scripts/ingest.py` (after any corpus, chunker or `bracu/text.py` change)
- Run locally: `uvicorn app:app --reload` → http://localhost:8000
- Retrieval test (free, question + synonym query): `python eval/evaluate.py` — the 46 original questions (incl. 3 Bangla) must keep their hit
- Retrieval through the query planner (~50 small LLM calls): `python eval/evaluate.py --plan` — 48/48
- Answer test (~250k tokens): `python eval/evaluate.py --answers`
- No API keys: `LLM_CHAIN=mock`

## Layout
- `app.py` FastAPI: `/api/chat` (SSE: search, sources, token, done|error), `/api/feedback`, `/api/health`; serves `public/` locally
- `public/index.html` chat page; `public/scene3d.js` optional Three.js background driven via `window.scene3d` (`setState`, `showSources`, `keepSources`, `pulse`, `setChatting`, `reset`) — the chat must keep working if it fails to load
- `bracu/retriever.py` BM25 computed in SQL from `bm25_postings` + pgvector cosine; RRF k=60; PRIORITY_WEIGHT; superseded filter; must-include rule; MAX_PER_DOC; word budget; rules-guide insertion. Parity with the original in-memory retriever was verified exactly.
- `bracu/query.py` "think first, then search": `plan()` = one fast LLM call (`PLANNER_CHAIN`, 300 tokens) → standalone question + 1–4 document-style queries, None if off/failed; `synonym_query()` = free student-word → document-word list. `rag.plan_search()` builds the query list; `Retriever.search(extra_queries=...)` fuses all lists with the same rules (no extras = identical to before).
- Settings: `QUERY_PLANNER`, `PLANNER_CHAIN`, `MAX_PLANNED_QUERIES`, `PLANNED_TOP_K` (k when 2+ planned queries).
- `bracu/text.py` tokenizer shared by ingest and search. `bracu/embedder.py` e5-small ONNX (query:/passage: prefixes, mean pooling, L2 norm).
- `bracu/llm.py` provider fallback chain (`LLM_CHAIN`), `bracu/prompts.py` system prompt + [S1] format, `bracu/guard.py` rate limits + feedback in Postgres.

## Rules
- Never commit or push. The owner does all git commits.
- Never commit `.env`; keys live in `.env` locally and in Vercel env vars.
- Keep corpus YAML front matter (`doc_id`, `status`, `rag_priority`, `superseded_by`). `00_START_HERE/current_rules_guide.md` decides conflicts.
- Answers must stay grounded: cite sources, prefer current rules, label drafts, say "not found" instead of guessing. Don't weaken `prompts.py`.
- Passages and questions must be embedded by the same `Embedder`; changing the model means re-running ingest.
- After changing retrieval or chunking, run `python eval/evaluate.py`.
- Synonyms apply to questions at search time only; never put them in `bracu/text.py` (the BM25 postings in Postgres are built with it).
