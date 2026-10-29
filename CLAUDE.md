# BRACU QUERTEX — FastAPI + pgvector

RAG chatbot over `BRACU_RAG_Corpus/` (199 docs, ~1,045 chunks) plus BRAC University announcements. FastAPI on Vercel (`sin1`), Neon Postgres + pgvector (`ap-southeast-1`). See README.md.

## Commands
- Setup: `pip install -r requirements-dev.txt -r requirements-sync.txt`, `python scripts/fetch_model.py`
- Build/refresh the index in Neon: `python scripts/ingest.py` (after any corpus, chunker or `bracu/text.py` change). Replaces corpus rows only; briefly locks the tables.
- Run locally: `uvicorn app:app --reload` → http://localhost:8000
- Retrieval test (free, question + synonym query): `python eval/evaluate.py` — 51/51; the 46 original questions (incl. 3 Bangla) must keep their hit
- Retrieval through the query planner (~50 small LLM calls): `python eval/evaluate.py --plan` — 51/51
- Answer test (~250k tokens): `python eval/evaluate.py --answers` (`--only q52,q53` for the announcement questions)
- Offline unit tests (fixtures only): `python -m unittest discover -s tests`
- Announcements: `python scripts/sync_search.py --dry-run` (Tavily search, max 5 credits a run; daily via `.github/workflows/search_sync.yml`), `python scripts/import_saved_pages.py --dry-run` (pages saved into `data/inbox/`)
- No API keys: `LLM_CHAIN=mock`

## Layout
- `app.py` FastAPI: `/api/chat` (SSE: search, sources, token, done|error), `/api/feedback`, `/api/announcements?limit=5`, `/api/health`; serves `public/` locally
- `public/index.html` chat page + "Latest notices" panel + theme switch (`<html data-theme>`, set before first paint from `localStorage.theme` or the device; dark tokens live in `:root[data-theme="dark"]`, never in a `prefers-color-scheme` media query) + Vercel Web Analytics snippet (page views only, never chat content; not loaded on localhost); `public/scene3d.js` optional Three.js background driven via `window.scene3d` (`setState`, `showSources`, `keepSources`, `pulse`, `setChatting`, `reset`) — the chat must keep working if it fails to load
- `bracu/retriever.py` BM25 computed in SQL from `bm25_postings` + pgvector cosine; RRF k=60; PRIORITY_WEIGHT; superseded filter; must-include rule; MAX_PER_DOC; word budget; rules-guide insertion. Parity with the original in-memory retriever was verified exactly. Announcements (`doc_type='announcement'`): ×1.1 under 90 days, ×0.8 to 365, older = like superseded, ×0.9 while `listed=false`; "latest" mode (notice word + recency word, incl. Bangla) puts the newest first. No announcement rows = identical to before.
- `bracu/query.py` "think first, then search": `plan()` = one fast LLM call (`PLANNER_CHAIN`, 300 tokens) → standalone question + 1–4 document-style queries, None if off/failed; `synonym_query()` = free student-word → document-word list. `rag.plan_search()` builds the query list; `Retriever.search(extra_queries=...)` fuses all lists with the same rules (no extras = identical to before).
- Settings: `QUERY_PLANNER`, `PLANNER_CHAIN`, `MAX_PLANNED_QUERIES`, `PLANNED_TOP_K` (k when 2+ planned queries), `LATEST_ANNOUNCEMENTS`. `config.today()` = Bangladesh date.
- `bracu/text.py` tokenizer shared by ingest and search. `bracu/embedder.py` e5-small ONNX (query:/passage: prefixes, mean pooling, L2 norm).
- `bracu/llm.py` provider fallback chain (`LLM_CHAIN`), `bracu/prompts.py` system prompt + [S1] format + today's date + `[past]` calendar rows + announcement labels (Published, Link, LINK ONLY), `bracu/guard.py` rate limits + feedback in Postgres.
- `sql/schema.sql` (safe to re-run): `chunks.origin` ('corpus' | 'search' | 'inbox'), `chunks.published_at`, `announcements` (url PK, listed, full_text, date_source, …), `refresh_bm25_stats()`.
- `scripts/announcements_store.py` the one write path for announcements: chunk → embed ("passage:") → tokenize → insert in one transaction + `refresh_bm25_stats()`, under an advisory lock that `ingest.py` also takes. Ids from 1,000,000; link-only never replaces full text; `listed` never goes back to false.
- `scripts/sync_search.py` Tavily search → provisional link-only entries (top-level slugs only, 60-day cutoff, privacy guard). `scripts/import_saved_pages.py` saved listing (confirm/add/reconcile) or post page (full text). Tests in `tests/`.

## Rules
- Never commit or push. The owner does all git commits.
- Only read and write files inside this project folder. Never open the owner's other folders. If a file is missing, ask the owner for it.
- Never commit `.env`; keys live in `.env` locally, in Vercel env vars, and in GitHub Actions secrets.
- Never send any request to bracu.ac.bd from code, and never crawl from Vercel: the site challenges bots (Cloudflare) and we don't bypass that. The Vercel app only reads the database.
- Tavily: search endpoint only — never extract, crawl or map, never `include_raw_content` or `include_answer`. `search_depth="basic"`, at most 5 searches a run (`--max-searches`), credits logged. `TAVILY_API_KEY` stays out of Vercel.
- Privacy: never store file results (PDFs, `/sites/default/files/…`) or snippets with personal data; fixtures must stay redacted. Errors printed by the sync go through `safe_error()` (Actions logs are public).
- `ingest.py` replaces only `origin='corpus'` rows; it must keep announcement rows (ids ≥ 1,000,000) and their postings. BM25 statistics come only from `refresh_bm25_stats()`.
- Sync and import packages (`tavily-python`, `beautifulsoup4`, `lxml`, PyYAML for the store) stay in `requirements-sync.txt`, never in `requirements.txt`.
- Keep corpus YAML front matter (`doc_id`, `status`, `rag_priority`, `superseded_by`). `00_START_HERE/current_rules_guide.md` decides conflicts.
- Answers must stay grounded: cite sources, prefer current rules, label drafts, say "not found" instead of guessing; link-only announcements: only their title/snippet, always the link. Don't weaken `prompts.py`.
- Passages and questions must be embedded by the same `Embedder`; changing the model means re-running ingest.
- After changing retrieval, chunking or announcement code, run `python eval/evaluate.py` and `python -m unittest discover -s tests`.
- Synonyms apply to questions at search time only; never put them in `bracu/text.py` (the BM25 postings in Postgres are built with it).
