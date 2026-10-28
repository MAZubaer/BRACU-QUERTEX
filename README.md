# BRACU QUERTEX — FastAPI + pgvector

A chatbot that answers BRAC University questions from 199 official documents. It puts current rules first, cites its sources, flags drafts, and says when it doesn't know.

This is the web version of the original Streamlit prototype:

| Part | Tool | Cost |
|---|---|---|
| API + chat page | FastAPI on Vercel (Hobby) | Free |
| Database, vector search, keyword search | Neon Postgres + pgvector | Free plan |
| Question embeddings | multilingual-e5-small, quantized ONNX (no PyTorch) | Free |
| Answers | Groq → Gemini → OpenRouter fallback chain | Free tiers (OpenRouter's gpt-oss, the last resort, costs a fraction of a cent per answer) |
| Rate limits + 👍/👎 feedback | Same Neon database | Free |

```
Your PC (once, and when documents change)          Vercel (live)
──────────────────────────────────────────          ─────────────────────────────────────
BRACU_RAG_Corpus/*.md                               app.py (FastAPI)
  │ scripts/chunker.py                                ├─ POST /api/chat     streams the answer
  ▼                                                   ├─ POST /api/feedback
data/chunks.jsonl                                     └─ GET  /api/health
  │ scripts/ingest.py  (embed + BM25 postings)      bracu/retriever.py
  ▼                                                   ├─ BM25 in SQL  ─┐
Neon Postgres ◄──────────────────────────────────────┤─ pgvector ──────┤→ fusion + status ranking
  chunks (+ vector), bm25_postings, bm25_terms,       └─ e5 ONNX model  │   (same rules as before)
  feedback, rate_limits                            public/index.html  (chat page)
```

**Think first, then search.** Students use different words than the documents ("how much will CSE cost me in total?" vs "tuition fee per credit"), and some answers need facts from several documents. So before searching, `bracu/query.py` makes one fast LLM call (the *query planner*, its own `PLANNER_CHAIN`, max 300 tokens). It returns the question as a standalone English question (follow-ups resolved, Bangla translated) and 1–4 short queries in the documents' wording, one per fact. The question, the standalone form and every planned query are all searched, and the results are fused with the same ranking rules. A free synonym list (student words → document words, e.g. *expense → fee tuition*) is always added too, and it is what the search falls back on if the planner is off or fails. The page shows "Searched for: …" above the sources. This adds about 1 second per question.

| Setting | Default | What it does |
|---|---|---|
| `QUERY_PLANNER` | `true` | Switch the planner off to search the old way (no extra LLM call) |
| `PLANNER_CHAIN` | `groq:openai/gpt-oss-20b,gemini:gemini-3.5-flash-lite,openrouter:openai/gpt-oss-20b` | Fast models for planning, tried left to right |
| `MAX_PLANNED_QUERIES` | `4` | Most queries kept from one plan |
| `PLANNED_TOP_K` | `7` | Passages sent to the LLM when the planner made 2+ queries (otherwise `TOP_K`) |

**Search quality is unchanged.** The SQL retriever was tested against the original in-memory `retriever.py` on all 48 eval questions plus 7 tricky cases (history, follow-ups, Bangla, drafts). It returned the same passages in the same order with the same scores, in both hybrid and keyword-only modes.

---

## 1. Set up on your PC (about 20 minutes, once)

You need Python 3.12 and Git.

```bash
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env                 # macOS/Linux: cp .env.example .env
```

### 1.1 Create the Neon database
1. Sign up at <https://neon.com> (free, no card).
2. Create a project. Choose region **AWS US East 1 (N. Virginia)**, next to Vercel's `iad1`.
3. Click **Connect**, turn on **Connection pooling**, and copy the connection string. Its host contains `-pooler`.
4. Paste it into `.env` as `DATABASE_URL=...`.

You don't need to create tables by hand. `ingest.py` runs `sql/schema.sql`, which also enables pgvector.

### 1.2 Add your API keys to `.env`
`GROQ_API_KEY` (<https://console.groq.com/keys>), and optionally `GEMINI_API_KEY` (<https://aistudio.google.com>) and `OPENROUTER_API_KEY` (<https://openrouter.ai/keys>). Chain entries without a key are skipped, so the defaults work with any of them.

### 1.3 Download the embedding model (~120 MB)
```bash
python scripts/fetch_model.py
```
This downloads the model into `model/`, split into parts under 90 MB so GitHub accepts them without Git LFS. At the end it prints a quick check: the "related" similarity should be clearly higher than the "unrelated" one.

### 1.4 Build the index in Neon
```bash
python scripts/ingest.py
```
This chunks the corpus, embeds the 1,041 chunks (a few minutes on a laptop CPU), builds the keyword index and uploads everything in one transaction. It uses about 30 MB of Neon's free storage. Re-run it whenever documents change. The live site keeps answering from the old data until the upload finishes.

### 1.5 Run it locally
```bash
uvicorn app:app --reload
```
Open <http://localhost:8000>. API docs are at <http://localhost:8000/api/docs>.

### 1.6 Check quality
```bash
python eval/evaluate.py              # retrieval + synonym query, free (no question may lose its hit)
python eval/evaluate.py --plan       # retrieval through the query planner, like the app (~50 small LLM calls)
python eval/evaluate.py --answers    # also checks the answers (~250k tokens; run rarely)
```
Each mode writes its own section of `eval/report.md`, so the synonym-only and planner results sit side by side.
The vectors now come from the ONNX version of e5-small, so check that retrieval still scores 46/46. Quantization changes the vectors very slightly.

---

## 2. Deploy on Vercel

1. Push the folder to GitHub. Commit `model/`, but make sure `.env` is **not** in the repository (`.gitignore` covers it).
2. Go to <https://vercel.com>, choose **Add New → Project**, and import the repository. Vercel detects FastAPI from `app.py`, so leave the build settings empty.
3. **Environment variables** (Settings → Environment Variables): `DATABASE_URL` (pooled), `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `IP_HASH_SALT` (any random text), and any setting from `.env.example` you changed.
   - Alternative: add Neon from Vercel's **Storage / Marketplace** tab. It fills in `DATABASE_URL` for you. Then point your local `.env` at that database and run `ingest.py` against it.
4. Click **Deploy**, then open `https://<project>.vercel.app/api/health`. Expect `"ok": true`, 1,041 chunks and `"embedder": "loaded"` after the first question.
5. Test on your phone. From now on, every `git push` redeploys.

**What to expect**
- **Function size:** the bundle stays well under Vercel's 500 MB Python limit. `.vercelignore` keeps the corpus, data and scripts out of the deployment.
- **Cold starts:** loading the model takes a second or two on a new instance. It starts loading in the background as soon as the instance starts. After 5 idle minutes, Neon's free plan pauses the database, and the first query wakes it, which adds a short delay. Streaming hides most of this.
- **Region:** `vercel.json` pins functions to `iad1`. Keep Neon in `us-east-1`.

---

## 3. Looking after it

| When | What to do |
|---|---|
| Weekly | Review 👎 answers: `SELECT * FROM feedback WHERE rating = -1 ORDER BY created_at DESC;` in Neon's SQL editor. Add failing questions to `eval/questions.jsonl`. |
| A document changes | Update the markdown in `BRACU_RAG_Corpus/` (keep the YAML: `status`, `rag_priority`, `superseded_by`) and `00_START_HERE/current_rules_guide.md`, then run `python scripts/ingest.py` and `python eval/evaluate.py`. No redeploy needed. |
| A model is retired | Change `LLM_CHAIN` in Vercel's environment variables and redeploy. |
| Free limits run out | Lower `RATE_LIMIT_*`, or add a paid key for one provider. |

## 4. Files

| Path | What it is |
|---|---|
| `app.py` | FastAPI app: chat (Server-Sent Events), feedback, health, serves `public/` locally |
| `bracu/retriever.py` | BM25 in SQL + pgvector, RRF fusion over all queries, status-aware ranking |
| `bracu/query.py` | Query planner (one fast LLM call) and the synonym list |
| `bracu/text.py` | Tokenizer/stemmer shared by ingest and search (re-run ingest if you change it) |
| `bracu/embedder.py` | e5-small with onnxruntime; falls back to keyword-only search if the model is missing |
| `bracu/llm.py`, `bracu/prompts.py`, `bracu/rag.py` | Provider fallback chain, system prompt, glue (same as the prototype) |
| `bracu/guard.py`, `bracu/db.py` | Rate limits, feedback, database connection |
| `public/index.html` | Chat page: streaming, clickable [S1] citations, status badges, 👍/👎, light/dark, glass UI |
| `public/scene3d.js` | 3D background (Three.js from jsDelivr): the 199 documents orbit a core; the passages sent to the AI fly forward in their status colour (same colours as the badges), and once the answer is done only the cited ones stay, matching the Sources list. Optional: the page works without WebGL, and shows still frames with reduced motion |
| `sql/schema.sql` | Tables: chunks, bm25_postings, bm25_terms, index_info, feedback, rate_limits |
| `scripts/ingest.py`, `scripts/chunker.py`, `scripts/fetch_model.py` | Build the index, download the model |
| `eval/` | 50 test questions and the evaluation script |

## 5. Troubleshooting

- **`/api/health` says `database: unavailable`**: check `DATABASE_URL` in Vercel. Use the pooled string and keep `?sslmode=require`.
- **`embedder: error ...`**: `model/` is missing from the deployment. Run `fetch_model.py`, commit `model/`, and push. Until then the bot uses keyword search only, which still works for English.
- **Every answer says "AI service is busy"**: the keys are missing or over their limits. `/api/health` shows which providers have a key.
- **Test without any API key**: set `LLM_CHAIN=mock`.
