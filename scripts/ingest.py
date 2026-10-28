"""Build the search index in Postgres (Neon). Run on your PC.

    python scripts/ingest.py                 # chunk corpus, embed, upload everything
    python scripts/ingest.py --no-chunk      # reuse data/chunks.jsonl as it is
    python scripts/ingest.py --keyword-only  # skip embeddings (no model needed)

Steps
1. Chunk BRACU_RAG_Corpus/ -> data/chunks.jsonl (scripts/chunker.py).
2. Embed every chunk's context_text with the ONNX e5 model (model/ folder).
3. Build BM25 postings with bracu/text.py's tokenizer.
4. In ONE transaction: create tables if needed, replace all rows, save index
   facts. The live site keeps answering from the old data until the commit.

Uses DATABASE_URL_UNPOOLED if set (the Vercel-Neon integration creates it),
otherwise DATABASE_URL.
"""
import argparse
import json
import math
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

COLS = ['id', 'chunk_id', 'doc_id', 'title', 'section', 'status', 'rag_priority', 'category',
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


def bm25_tables(chunks):
    tfs = [Counter(tokenize(c['context_text'])) for c in chunks]
    lens = [sum(tf.values()) for tf in tfs]
    df = Counter(t for tf in tfs for t in tf)
    n = len(chunks)
    idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
    avg = sum(lens) / n if n else 1.0
    return tfs, lens, idf, avg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-chunk', action='store_true', help='use data/chunks.jsonl as it is')
    ap.add_argument('--keyword-only', action='store_true', help='do not store embeddings')
    ap.add_argument('--embeddings', help='reuse vectors from a .npy file (testing)')
    ap.add_argument('--database-url', help='override DATABASE_URL')
    args = ap.parse_args()

    if not args.no_chunk and config.CORPUS_DIR.exists():
        import chunker
        config.CHUNKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        print(f'Chunking {config.CORPUS_DIR} ...')
        chunker.main(str(config.CORPUS_DIR), str(config.CHUNKS_FILE))
    chunks = load_chunks(config.CHUNKS_FILE)
    print(f'{len(chunks)} chunks')

    vecs = None if args.keyword_only else embed(chunks, args.embeddings)
    tfs, lens, idf, avg = bm25_tables(chunks)
    n_post = sum(len(tf) for tf in tfs)
    print(f'BM25: {len(idf)} terms, {n_post} postings, avg length {avg:.1f}')

    url = args.database_url or os.environ.get('DATABASE_URL_UNPOOLED') or config.DATABASE_URL
    t = time.time()
    with connect(url) as conn:
        conn.autocommit = False
        with conn.transaction(), conn.cursor() as cur:
            cur.execute((ROOT / 'sql' / 'schema.sql').read_text(encoding='utf-8'))
            cur.execute('TRUNCATE chunks, bm25_postings, bm25_terms')
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
            with cur.copy('COPY bm25_terms (term, idf) FROM STDIN') as cp:
                for term, v in idf.items():
                    cp.write_row([term, v])
            build = {'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                     'chunks': len(chunks),
                     'embedding_model': config.EMBED_MODEL if vecs is not None else None,
                     'documents': len({c['doc_id'] for c in chunks})}
            for key, value in (('bm25', {'avg_len': avg, 'n': len(chunks), 'k1': 1.5, 'b': 0.75}),
                               ('build', build)):
                cur.execute("""INSERT INTO index_info (key, value) VALUES (%s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                            (key, json.dumps(value)))
        conn.autocommit = True
        conn.execute('ANALYZE chunks')
        conn.execute('ANALYZE bm25_postings')
        conn.execute('ANALYZE bm25_terms')
    print(f'Uploaded to Postgres in {time.time() - t:.1f}s. Done.')


if __name__ == '__main__':
    main()
