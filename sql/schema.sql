-- BRACU QUERTEX: Postgres + pgvector schema (Neon).
-- scripts/ingest.py runs this automatically; you can also paste it into
-- the Neon SQL Editor. Safe to run more than once.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per chunk. "id" is the chunk's position in data/chunks.jsonl.
CREATE TABLE IF NOT EXISTS chunks (
    id              integer PRIMARY KEY,
    chunk_id        text UNIQUE NOT NULL,
    doc_id          text NOT NULL,
    title           text,
    section         text,
    status          text NOT NULL,         -- current, draft_not_approved, superseded, ...
    rag_priority    integer NOT NULL DEFAULT 2,
    category        text,
    doc_type        text,
    source_type     text,
    url             text,
    source          text,
    approved_on     text,
    effective_date  text,
    superseded_by   jsonb NOT NULL DEFAULT '[]',
    file            text,
    chunk_index     integer,
    words           integer NOT NULL,
    text            text NOT NULL,
    context_text    text NOT NULL,          -- what is embedded and keyword-indexed
    bm25_len        integer NOT NULL,       -- number of BM25 tokens
    embedding       vector(384)             -- multilingual-e5-small, normalised
);
CREATE INDEX IF NOT EXISTS chunks_doc_id_idx ON chunks (doc_id);
-- Where a chunk comes from: 'corpus' (ingest.py, ids 0..n-1) or an announcement
-- source, 'search' (sync_search.py) / 'inbox' (import_saved_pages.py), ids from
-- 1,000,000. ingest.py replaces only the corpus rows.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'corpus';
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS published_at date;
CREATE INDEX IF NOT EXISTS chunks_origin_idx ON chunks (origin);
-- ~1,000 rows: an exact scan is a few milliseconds and gives exact results.
-- Past ~50,000 chunks, add an approximate index:
--   CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);

-- BM25 keyword index, built by ingest.py with bracu/text.py's tokenizer,
-- so keyword ranking is identical to the original Python BM25.
CREATE TABLE IF NOT EXISTS bm25_postings (
    term   text    NOT NULL,
    chunk  integer NOT NULL,
    tf     integer NOT NULL,
    PRIMARY KEY (term, chunk)
);
CREATE TABLE IF NOT EXISTS bm25_terms (
    term  text PRIMARY KEY,
    idf   double precision NOT NULL
);

-- Index facts: BM25 average length, embedding model, build time.
CREATE TABLE IF NOT EXISTS index_info (
    key    text PRIMARY KEY,
    value  jsonb NOT NULL
);

-- BM25 statistics over ALL chunks and postings (corpus + announcements), same
-- formula as the original Python BM25: idf = ln(1 + (n - df + 0.5) / (df + 0.5)).
-- Called by ingest.py, sync_search.py and import_saved_pages.py after writing.
-- avg_len goes through text so the stored value is the exact float8.
CREATE OR REPLACE FUNCTION refresh_bm25_stats() RETURNS void LANGUAGE sql AS $$
    TRUNCATE bm25_terms;
    INSERT INTO bm25_terms (term, idf)
    SELECT p.term, ln(1 + (n.n - count(*)::float8 + 0.5) / (count(*)::float8 + 0.5))
    FROM bm25_postings p CROSS JOIN (SELECT count(*)::float8 AS n FROM chunks) n
    GROUP BY p.term, n.n;
    INSERT INTO index_info (key, value)
    SELECT 'bm25', jsonb_build_object(
               'avg_len', COALESCE((sum(bm25_len)::float8 / NULLIF(count(*), 0))::text::numeric, 1),
               'n', count(*), 'k1', 1.5, 'b', 0.75)
    FROM chunks
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
$$;

-- BRAC University announcements: the posts listed on
-- https://www.bracu.ac.bd/news-archive/announcements. One row per post URL;
-- its chunks are in `chunks` with doc_id = announcements.doc_id.
--   source      'inbox' (a page saved by hand) or 'search' (Tavily search result)
--   listed      true once the post was seen on a saved announcements listing page;
--               search results stay listed=false (provisional) until then
--   full_text   true when the post's own page was imported; false = link only
--   date_source 'page' (Publish Date / article meta), 'title', 'snippet', 'search'
--               (Tavily published_date) or 'found' (the day we found it)
CREATE TABLE IF NOT EXISTS announcements (
    url           text PRIMARY KEY,
    section       text NOT NULL DEFAULT 'announcements',
    title         text NOT NULL,
    published_at  date,
    date_source   text,
    first_seen    timestamptz NOT NULL DEFAULT now(),
    last_seen     timestamptz NOT NULL DEFAULT now(),
    source        text NOT NULL,
    snippet       text,
    full_text     boolean NOT NULL DEFAULT false,
    listed        boolean NOT NULL DEFAULT false,
    listed_at     timestamptz,
    content_hash  text,
    pdf_links     jsonb NOT NULL DEFAULT '[]',
    note          text,
    doc_id        text NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS announcements_published_idx ON announcements (published_at DESC);

-- Thumbs up/down from the chat page (no personal data).
CREATE TABLE IF NOT EXISTS feedback (
    id          bigserial PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    rating      smallint NOT NULL CHECK (rating IN (-1, 1)),
    question    text NOT NULL,
    answer      text NOT NULL,
    source_ids  text[] NOT NULL DEFAULT '{}',
    provider    text,
    comment     text
);

-- Fixed-window rate limits per hashed IP.
CREATE TABLE IF NOT EXISTS rate_limits (
    key         text NOT NULL,
    bucket      text NOT NULL,
    count       integer NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (key, bucket)
);
