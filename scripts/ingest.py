"""Build the search index in Postgres (Neon). Run on your PC.

    python scripts/ingest.py                 # chunk corpus, embed, upload everything
    python scripts/ingest.py --no-chunk      # reuse data/chunks.jsonl as it is
    python scripts/ingest.py --keyword-only  # skip embeddings (no model needed)
    python scripts/ingest.py --rebuild-postings  # after a tokenizer change: announcement
                                                 # chunks are re-tokenized too (not re-embedded)

Steps
1. Chunk BRACU_RAG_Corpus/ -> data/chunks.jsonl (scripts/chunker.py).
2. Embed every chunk's context_text with the ONNX e5 model (model/ folder).
3. Build BM25 postings with bracu/text.py's tokenizer.
4. In ONE transaction: create tables if needed, replace the corpus rows
   (origin='corpus'; announcement rows from the sync/import are kept), refresh
   the BM25 statistics over all rows, save index facts. The live site keeps
   answering from the old data until the commit.

Uses DATABASE_URL_UNPOOLED if set (the Vercel-Neon integration creates it),
otherwise DATABASE_URL.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from bracu import config                    # noqa: E402
from bracu.db import connect                 # noqa: E402
from bracu.embedder import to_pgvector       # noqa: E402
from bracu.text import tokenize              # noqa: E402

from announcements_store import ID_START as ANNOUNCEMENT_ID_START, LOCK_KEY  # noqa: E402

COLS = ['id','chunk_id', 'doc_id', 'title', 'section', 'status', 'rag_priority', 'category',
        'doc_type', 'source_type', 'url', 'source', 'approved_on', 'effective_date',
        'superseded_by', 'file', 'chunk_index', 'words', 'text', 'context_text',
        'bm25_len', 'embedding']


def load_chunks(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def embed(chunks, npy=None):
    import numpy as np
    if npy:
        vecs = np.load(npy).astype('float32')
        if len(vecs) != len(chunks):
            sys.exit(f'{npy} has {len(vecs)} vectors but there are {len(chunks)} chunks.')
        print(f'Using vectors from {npy} (they must come from the same model as the API!)')
        return vecs
    from bracu.embedder import Embedder
    try:
        emb = Embedder()
    except FileNotFoundError as e:
        sys.exit(f'{e}\nOr run with --keyword-only.')
    t = time.time()
    texts = [c['context_text'] for c in chunks]
    out = []
    for start in range(0, len(texts), 64):
        out.append(emb.encode(texts[start:start + 64], kind='passage'))
        print(f'  embedded {min(start + 64, len(texts))}/{len(texts)}', end='\r', flush=True)
    vecs = np.vstack(out)
    print(f'\nEmbedded {len(vecs)} chunks in {time.time() - t:.0f}s')
    return vecs


def bm25_postings(chunks):
    """Term counts per chunk; idf and the average length are computed in SQL
    (refresh_bm25_stats) over all chunks, corpus and announcements."""
    tfs = [Counter(tokenize(c['context_text'])) for c in chunks]
    return tfs, [sum(tf.values()) for tf in tfs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-chunk', action='store_true', help='use data/chunks.jsonl as it is')
    ap.add_argument('--keyword-only', action='store_true', help='do not store embeddings')
    ap.add_argument('--embeddings', help='reuse vectors from a .npy file (testing)')
    ap.add_argument('--database-url', help='override DATABASE_URL')
    ap.add_argument('--rebuild-postings', action='store_true',
                    help='also re-tokenize the kept announcement chunks (after a bracu/text.py change)')
    args = ap.parse_args()

    if not args.no_chunk and config.CORPUS_DIR.exists():
        import chunker
        config.CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        print(f'Chunking {config.CORPUS_DIR} ...')
        chunker.main(str(config.CORPUS_DIR), str(config.CHUNKS_FILE))
    chunks = load_chunks(config.CHUNKS_FILE)
    print(f'{len(chunks)} chunks')

    if len(chunks) >= ANNOUNCEMENT_ID_START:
        sys.exit(f'{len(chunks)} corpus chunks would reach the announcement ids ({ANNOUNCEMENT_ID_START}).')
    vecs = None if args.keyword_only else embed(chunks, args.embeddings)
    tfs, lens = bm25_postings(chunks)
    print(f'BM25: {sum(len(tf) for tf in tfs)} corpus postings')

    url = args.database_url or os.environ.get('DATABASE_URL_UNPOOLED') or config.DATABASE_URL
    t = time.time()
    with connect(url) as conn:
        conn.autocommit = False
        with conn.transaction(), conn.cursor() as cur:
            cur.execute((ROOT / 'sql' / 'schema.sql').read_text(encoding='utf-8'))
            # the sync/import writers take this lock too: none of their rows can be
            # written between setting them aside and the TRUNCATE below
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (LOCK_KEY,))
            # Replace the corpus only: announcement rows (ids >= 1,000,000) are set
            # aside, the tables truncated (no dead rows, unlike a 175k-row DELETE)
            # and the announcement rows put back.
            cur.execute(f"""CREATE TEMP TABLE keep_chunks ON COMMIT DROP AS
                            SELECT * FROM chunks WHERE id >= {ANNOUNCEMENT_ID_START}""")
            cur.execute(f"""CREATE TEMP TABLE keep_postings ON COMMIT DROP AS
                            SELECT * FROM bm25_postings WHERE chunk >= {ANNOUNCEMENT_ID_START}""")
            cur.execute('TRUNCATE chunks, bm25_postings')
            cur.execute('INSERT INTO chunks SELECT * FROM keep_chunks')
            if args.rebuild_postings:
                # tokenizer changed: re-tokenize the kept announcement chunks from their
                # stored context_text (their vectors don't depend on the tokenizer)
                kept = cur.execute('SELECT id, context_text FROM keep_chunks').fetchall()
                with cur.copy('COPY bm25_postings (term, chunk, tf) FROM STDIN') as cp:
                    for i, text in kept:
                        tf = Counter(tokenize(text))
                        for term, n in tf.items():
                            cp.write_row([term, i, n])
                cur.executemany('UPDATE chunks SET bm25_len = %s WHERE id = %s',
                                [(len(tokenize(text)), i) for i, text in kept])
                print(f'Re-tokenized {len(kept)} announcement chunks')
            else:
                cur.execute('INSERT INTO bm25_postings SELECT * FROM keep_postings')
            with cur.copy(f"COPY chunks ({', '.join(COLS)}) FROM STDIN") as cp:
                for i, c in enumerate(chunks):
                    cp.write_row([
                        i, c['chunk_id'], c['doc_id'], c.get('title', ''), c.get('section', ''),
                        c['status'], int(c.get('rag_priority', 2)), c.get('category', ''),
                        c.get('doc_type', ''), c.get('source_type', ''), c.get('url', ''),
                        str(c.get('source', '')), c.get('approved_on', ''),
                        c.get('effective_date', ''),
                        json.dumps(c.get('superseded_by') or [], ensure_ascii=False),
                        c.get('file', ''), c.get('chunk_index', 0), c['words'], c['text'],
                        c['context_text'], lens[i],
                        to_pgvector(vecs[i]) if vecs is not None else None])
            with cur.copy('COPY bm25_postings (term, chunk, tf) FROM STDIN') as cp:
                for i, tf in enumerate(tfs):
                    for term, count in tf.items():
                        cp.write_row([term, i, count])
            cur.execute('SELECT refresh_bm25_stats()')
            build = {'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     'chunks': len(chunks),
                     'embedding_model': config.EMBED_MODEL if vecs is not None else None,
                     'documents': len({c['doc_id'] for c in chunks})}
            cur.execute("""INSERT INTO index_info (key, value) VALUES ('build', %s)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                        (json.dumps(build),))
            cur.execute("""SELECT (SELECT count(*) FROM bm25_terms),
                                  (SELECT count(*) FROM chunks WHERE origin <> 'corpus'),
                                  (SELECT value FROM index_info WHERE key = 'bm25')""")
            n_terms, n_other, bm25 = cur.fetchone()
        conn.autocommit = True
        conn.execute('ANALYZE chunks')
        conn.execute('ANALYZE bm25_postings')
        conn.execute('ANALYZE bm25_terms')
    print(f'BM25 over all {bm25["n"]} chunks ({n_other} announcement chunks kept): '
          f'{n_terms} terms, avg length {bm25["avg_len"]:.1f}')
    print(f'Uploaded to Postgres in {time.time() - t:.1f}s. Done.')


if __name__ == '__main__':
    main()
