<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="brand/svg/quertex-q-horizontal-dark.svg">
    <img src="brand/svg/quertex-q-horizontal-light.svg" alt="BRACU QUERTEX" height="80">
  </picture>
</p>

# BRACU QUERTEX — FastAPI + pgvector

A chatbot that answers BRAC University questions from 199 official documents and the university's latest announcements. It puts current rules first, cites its sources, labels draft versions (policies published by BRAC University without a stated final approval date), and says when it doesn't know.

This is the web version of the original Streamlit prototype:

| Part | Tool | Cost |
|---|---|---|
| API + chat page | FastAPI on Vercel (Hobby) | Free |
| Database, vector search, keyword search | Neon Postgres + pgvector | Free plan |
| Question embeddings | multilingual-e5-small, quantized ONNX (no PyTorch) | Free |
| Answers | Groq → Gemini → OpenRouter fallback chain | Free tiers (OpenRouter's gpt-oss, the last resort, costs a fraction of a cent per answer) |
| Rate limits + 👍/👎 feedback | Same Neon database | Free |
| Announcements | Tavily search (daily, GitHub Actions) + pages you save in your browser | Free (Tavily: 1,000 credits a month) |

```
Your PC                                    GitHub Actions (daily)       Vercel (live, read-only)
──────────────────────────────────         ─────────────────────        ─────────────────────────────────
BRACU_RAG_Corpus/*.md                      scripts/sync_search.py       app.py (FastAPI)
  │ scripts/chunker.py                       Tavily SEARCH only:          ├─ POST /api/chat
  ▼                                          titles, links, snippets      ├─ POST /api/feedback
data/chunks.jsonl                            of new bracu.ac.bd posts     ├─ GET  /api/announcements
  │ scripts/ingest.py (embed + BM25)           │                          └─ GET  /api/health
  ▼                                            ▼                        bracu/retriever.py
Neon Postgres ◄────────────────────────────────┴──────────────────────────┤ BM25 in SQL + pgvector
  chunks (+ vector), bm25_postings, bm25_terms,                            │ → fusion, status + date ranking
  announcements, feedback, rate_limits                                     └ e5 ONNX model
  ▲                                                                     public/index.html (chat page,
data/inbox/*.html (pages you saved with Ctrl+S)                          "Latest notices" panel)
  └ scripts/import_saved_pages.py
```

**Think first, then search.** Students use different words than the documents ("how much will CSE cost me in total?" vs "tuition fee per credit"), and some answers need facts from several documents. So before searching, `bracu/query.py` makes one fast LLM call (the *query planner*, its own `PLANNER_CHAIN`, max 300 tokens). It returns the question as a standalone English question (follow-ups resolved, Bangla translated) and 1–4 short queries in the documents' wording, one per fact. The question, the standalone form and every planned query are all searched, and the results are fused with the same ranking rules. A free synonym list (student words → document words, e.g. *expense → fee tuition*) is always added too, and it is what the search falls back on if the planner is off or fails. The page shows "Searched for: …" above the sources. This adds about 1 second per question.

| Setting | Default | What it does |
|---|---|---|
| `QUERY_PLANNER` | `true` | Switch the planner off to search the old way (no extra LLM call) |
| `PLANNER_CHAIN` | `groq:openai/gpt-oss-20b,gemini:gemini-3.5-flash-lite,openrouter:openai/gpt-oss-20b` | Fast models for planning, tried left to right |
| `MAX_PLANNED_QUERIES` | `4` | Most queries kept from one plan |
| `PLANNED_TOP_K` | `7` | Passages sent to the LLM when the planner made 2+ queries (otherwise `TOP_K`) |

**Search quality is unchanged.** The SQL retriever was tested against the original in-memory `retriever.py` on all 48 eval questions plus 7 tricky cases (history, follow-ups, Bangla, drafts). It returned the same passages in the same order with the same scores, in both hybrid and keyword-only modes. With no announcements in the database, the results are still exactly the same.

**Dates.** Every question tells the model today's date (Bangladesh time). Year Planner rows that are already over are marked `[past]` before the model sees them, so "when is the next holiday?" never depends on the model's date arithmetic.

---

## 1. Set up on your PC (about 20 minutes, once)

You need Python 3.12 and Git.

```bash
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r requirements-sync.txt   # only for the announcement scripts and tests
copy .env.example .env                 # macOS/Linux: cp .env.example .env
```

### 1.1 Create the Neon database
1. Sign up at <https://neon.com> (free, no card).
2. Create a project. Choose region **AWS Asia Pacific 1 (Singapore), `ap-southeast-1`**, next to Vercel's `sin1`, where `vercel.json` runs the functions.
3. Click **Connect**, turn on **Connection pooling**, and copy the connection string. Its host contains `-pooler`. Paste it into `.env` as `DATABASE_URL=...`.
4. Turn pooling off and copy the direct string too. Paste it as `DATABASE_URL_UNPOOLED=...`. The scripts use it for bulk writes.

You don't need to create tables by hand. `ingest.py` runs `sql/schema.sql`, which also enables pgvector. The schema is safe to run again.

### 1.2 Add your API keys to `.env`
`GROQ_API_KEY` (<https://console.groq.com/keys>), and optionally `GEMINI_API_KEY` (<https://aistudio.google.com>) and `OPENROUTER_API_KEY` (<https://openrouter.ai/keys>). Chain entries without a key are skipped, so the defaults work with any of them. For the announcement search, add `TAVILY_API_KEY` (<https://app.tavily.com>). The live site doesn't need it.

### 1.3 Download the embedding model (~120 MB)
```bash
python scripts/fetch_model.py
```
This downloads the model into `model/`, split into parts under 90 MB so GitHub accepts them without Git LFS. At the end it prints a quick check: the "related" similarity should be clearly higher than the "unrelated" one.

### 1.4 Build the index in Neon
```bash
python scripts/ingest.py
```
This chunks the corpus, embeds the ~1,045 chunks (a few minutes on a laptop CPU) and builds the keyword postings. Then, in **one transaction**, it:
1. sets the announcement rows aside (chunk ids from 1,000,000),
2. empties `chunks` and `bm25_postings` with `TRUNCATE`, which leaves no dead rows behind,
3. loads the new corpus chunks and puts the announcement rows back,
4. recomputes the BM25 statistics over all chunks (`refresh_bm25_stats()` in SQL).

Only the corpus is replaced; announcements are never touched. It uses about 25 MB of Neon's free storage. While the transaction runs, the `TRUNCATE` locks the tables, so live questions wait a few seconds and are then answered from the new data.

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
python -m unittest discover -s tests # offline tests of the announcement scripts and rules (no network)
```
Each mode writes its own section of `eval/report.md`, so the synonym-only and planner results sit side by side. Current results: **51/51** in both modes (MRR 0.88 free, 0.92 with the planner). The 46 original questions, including the 3 Bangla ones, must keep their hit.

---

## 2. Deploy on Vercel

1. Push the folder to GitHub. Commit `model/`, but make sure `.env` is **not** in the repository (`.gitignore` covers it).
2. Go to <https://vercel.com>, choose **Add New → Project**, and import the repository. Vercel detects FastAPI from `app.py`, so leave the build settings empty.
3. **Environment variables** (Settings → Environment Variables): `DATABASE_URL` (pooled), `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `IP_HASH_SALT` (any random text), and any setting from `.env.example` you changed. Vercel needs neither `TAVILY_API_KEY` nor the unpooled URL: the live site only reads the database.
   - Alternative: add Neon from Vercel's **Storage / Marketplace** tab. It fills in `DATABASE_URL` for you. Then point your local `.env` at that database and run `ingest.py` against it.
4. Click **Deploy**, then open `https://<project>.vercel.app/api/health`. Expect `"ok": true`, the chunk count (about 1,045 corpus chunks plus the announcements) and `"embedder": "loaded"` after the first question. `/api/announcements` lists the newest notices.
5. **Web Analytics:** in the Vercel project, open the **Analytics** tab and click **Enable**. The page already loads Vercel's script (`/_vercel/insights/script.js`, skipped on localhost). It counts page views, visitors, referrers, countries and devices, anonymously and without cookies. Data shows up after the next deploy and a few visits. The free Hobby plan includes page views; custom events need a paid plan.
6. Test on your phone. From now on, every `git push` redeploys.

**What to expect**
- **Function size:** the bundle stays well under Vercel's 500 MB Python limit. `.vercelignore` keeps the corpus, data, scripts, tests and `.github/` out of the deployment.
- **Cold starts:** loading the model takes a second or two on a new instance. It starts loading in the background as soon as the instance starts. After 5 idle minutes, Neon's free plan pauses the database, and the first query wakes it, which adds a short delay. Streaming hides most of this.
- **Region:** `vercel.json` pins functions to `sin1` (Singapore). Keep Neon in `ap-southeast-1` (Singapore) too: every question makes a few database round trips.

---

## 3. Announcements

"Announcements" means exactly the posts listed on <https://www.bracu.ac.bd/news-archive/announcements>. The bot answers "what are the latest announcements?", gives each post's publication date and link, and shows a **Latest notices** panel on the start screen.

### Why there is no crawler
bracu.ac.bd answers automated requests with a Cloudflare challenge ("Just a moment…"). The site has told bots no, and getting around that, even through a third party that fetches pages for us, would be bypassing their protection. So **no code in this project ever sends a request to bracu.ac.bd**. The Vercel app only reads the database. Announcements come from two sources that don't fetch BRACU's pages:

| Source | What it stores | How |
|---|---|---|
| **A. Tavily search** (automatic, daily) | Title, link, a short snippet and the date of new posts, from Tavily's search index (like a search engine result) | `scripts/sync_search.py`, run by GitHub Actions. Search endpoint only: never Tavily's extract, crawl or map, never raw page content |
| **B. Your inbox** (manual) | The announcements **listing** (the official list) or a single post's **full text** | You save the page in your browser; `scripts/import_saved_pages.py` reads the file |

Each post moves through these states:

| State | Comes from | Search weight | On the page |
|---|---|---|---|
| Provisional (`listed=false`) | Search | ×0.9, priority 3 | "link only" + "unconfirmed" tags |
| Confirmed (`listed=true`) | A saved listing page | priority 2 | "link only" tag |
| Full text (`full_text=true`) | A saved post page | priority 2 | No tag |

On top of that, announcements rank ×1.1 when under 90 days old and ×0.8 up to a year. Older ones are left out like superseded documents, unless the question is about history. For link-only posts the model may only repeat the title and snippet and must give the link; details such as times, rooms and deadlines are in the notice itself.

**Privacy:** file results (PDFs, `/sites/default/files/…`) are never stored. A search snippet that looks like a list of people (student IDs, student emails, phone numbers) is dropped, keeping only the title and link. A saved post page with more than 5 such details is kept link-only; fewer are replaced with `[removed]`.

### A. Daily search sync (GitHub Actions)
`.github/workflows/search_sync.yml` runs every day at 7:30 am Dhaka (01:30 UTC). It runs the offline tests, then `python scripts/sync_search.py`. Only one sync runs at a time, with a 30-minute limit.

**Secrets** (GitHub → repository **Settings → Secrets and variables → Actions → New repository secret**):

| Secret | Value |
|---|---|
| `DATABASE_URL_UNPOOLED` | Neon's direct (unpooled) connection string |
| `TAVILY_API_KEY` | Your Tavily API key |

**Run it by hand:** **Actions → Announcements search sync → Run workflow.** You can choose `time_range` (day/week/month), `max_searches` (1–5) and `dry_run`. Locally:
```bash
python scripts/sync_search.py --dry-run                  # what it would add; still uses credits
python scripts/sync_search.py --time-range month --max-searches 2
```
The log shows every result kept or skipped and why (URLs only), and the credits used.

**Is it working?**
- **Failure emails:** GitHub emails you when a scheduled run fails. No email means it's running.
- **Run history:** **Actions → Announcements search sync** lists every run with its time and ✅/❌.
- **From the app:** `/api/health` shows `last_search_sync`: the time (Dhaka), the trigger (`schedule`, `workflow_dispatch` or `local`), searches, credits, results, new and already stored, for the last successful real run. Dry runs aren't recorded.
- **Timing:** GitHub starts scheduled runs on a best-effort basis. They're often minutes late, sometimes hours on busy days or on a new schedule's first day, and occasionally skipped. For a run right now, use **Run workflow**.
- **Inactivity:** in a public repo, GitHub turns scheduled workflows off after 60 days without any commit. If that happens, re-enable it on the Actions page.

What it keeps: posts at a top-level `www.bracu.ac.bd/<slug>` URL. Query strings are stripped first. It drops `/news/`, `/event/`, section and listing pages, other subdomains, files, and anything dated more than 60 days ago. Dates come from Tavily, else the snippet ("Publish Date: …"), else the title, else the day it was found (shown as "around <date>").

**Credit budget:** the free plan has 1,000 credits a month, and a basic search costs 1. A run makes at most 5 searches, so the daily sync uses about 150 a month. Dry runs and manual runs cost the same as real ones.

### B. Inbox routine (full text and the official list)
1. In your browser, open the announcements listing (and any notice you want in full), let it load, and save it with **Ctrl+S → "Webpage, HTML only"** into `data/inbox/`.
2. Run:
   ```bash
   python scripts/import_saved_pages.py --dry-run   # shows what it will confirm, add, delete
   python scripts/import_saved_pages.py
   ```
3. Imported files move to `data/inbox/done/`. Files it can't use go to `data/inbox/failed/`, with a `.txt` file saying why.

**A saved listing page:**
- **Each listed post** is created or confirmed, with its exact title and Publish Date.
- **Provisional search entries are deleted** when they fall in that page's date range but aren't on the page. The range runs from the oldest date (exclusive, because those posts may continue on the next page; inclusive on the last page) to the newest (inclusive).
- **Never deleted:** full-text imports, and entries found by search after you saved the file.

**A saved post page:**
- **Its main text replaces the link-only entry.** That's the Drupal `.pane-node-body .field-item` block, with tables kept one line per row and Cloudflare-hidden emails decoded.
- **PDF links are recorded**, never downloaded.

`data/` is in `.gitignore`, so saved pages never reach GitHub. Saving the listing once a week keeps the "unconfirmed" list short.

---

## 4. Looking after it

| When | What to do |
|---|---|
| Weekly | Save the announcements listing into `data/inbox/` and run `python scripts/import_saved_pages.py`. Review 👎 answers: `SELECT * FROM feedback WHERE rating = -1 ORDER BY created_at DESC;` in Neon's SQL editor. Add failing questions to `eval/questions.jsonl`. |
| A document changes | Update the markdown in `BRACU_RAG_Corpus/` (keep the YAML: `status`, `rag_priority`, `superseded_by`) and `00_START_HERE/current_rules_guide.md`, then run `python scripts/ingest.py` and `python eval/evaluate.py`. No redeploy needed. The ingest briefly locks the tables (see 1.4): live questions wait a few seconds. Announcements are kept. |
| GitHub emails "sync failed" | Open the run's log. `Tavily search failed` means a bad key or no credits left. `Database unavailable` means the `DATABASE_URL_UNPOOLED` secret is wrong or Neon is down. The log never shows the key or the database host. |
| A model is retired | Change `LLM_CHAIN` in Vercel's environment variables and redeploy. |
| Free limits run out | Lower `RATE_LIMIT_*`, or add a paid key for one provider. For Tavily, lower `max_searches` or run the sync less often. |

## 5. Files

| Path | What it is |
|---|---|
| `app.py` | FastAPI app: chat (Server-Sent Events), feedback, announcements, health; serves `public/` locally |
| `bracu/retriever.py` | BM25 in SQL + pgvector, RRF fusion over all queries, status-aware ranking, announcement date weights and "latest" mode |
| `bracu/query.py` | Query planner (one fast LLM call) and the synonym list |
| `bracu/text.py` | Tokenizer/stemmer shared by ingest and search (re-run ingest if you change it) |
| `bracu/embedder.py` | e5-small with onnxruntime; falls back to keyword-only search if the model is missing |
| `bracu/llm.py`, `bracu/prompts.py`, `bracu/rag.py` | Provider fallback chain; system prompt, today's date, `[past]` marks and announcement labels; glue |
| `bracu/config.py`, `bracu/guard.py`, `bracu/db.py` | Settings, rate limits, feedback, database connection |
| `public/index.html` | Chat page: streaming, clickable [S1] citations, status badges, 👍/👎, glass UI, Latest notices panel. Light/dark: follows the device until someone uses the sun/moon switch in the header (remembered in that browser; Shift+click follows the device again) |
| `public/scene3d.js` | 3D background (Three.js from jsDelivr): the 199 documents orbit a core; the passages sent to the AI fly forward in their status colour (same colours as the badges), and once the answer is done only the cited ones stay, matching the Sources list. Optional: the page works without WebGL, and shows still frames with reduced motion |
| `sql/schema.sql` | Tables: chunks, bm25_postings, bm25_terms, index_info, announcements, feedback, rate_limits; `refresh_bm25_stats()` |
| `scripts/ingest.py`, `scripts/chunker.py`, `scripts/fetch_model.py` | Build the corpus index, download the model |
| `scripts/sync_search.py`, `scripts/import_saved_pages.py`, `scripts/announcements_store.py` | Announcement search sync, inbox import, and the shared chunk → embed → insert code |
| `scripts/test_tavily.py` | One-off Tavily check that saved the test fixtures (4 credits) |
| `.github/workflows/search_sync.yml` | The daily search sync |
| `requirements-sync.txt` | Packages for the announcement scripts only (not installed on Vercel) |
| `eval/` | 53 test questions and the evaluation script |
| `tests/` | Offline unit tests and their fixtures (saved Tavily results with personal data redacted, a saved listing page, a Cloudflare challenge page) |

## 6. Troubleshooting

- **`/api/health` says `database: unavailable`**: check `DATABASE_URL` in Vercel. Use the pooled string and keep `?sslmode=require`.
- **`embedder: error ...`**: `model/` is missing from the deployment. Run `fetch_model.py`, commit `model/`, and push. Until then the bot uses keyword search only, which still works for English.
- **Every answer says "AI service is busy"**: the keys are missing or over their limits. `/api/health` shows which providers have a key.
- **Test without any API key**: set `LLM_CHAIN=mock`.
- **The Latest notices panel is missing**: there are no announcements from the last year in the database yet (or the database is down). Run the sync or import a saved listing.
- **A saved page went to `data/inbox/failed/`**: read the `.txt` next to it. "Cloudflare challenge page" means the page hadn't finished loading. Open it again, wait, and save again.
