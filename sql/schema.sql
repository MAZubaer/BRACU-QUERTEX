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
