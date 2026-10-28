"""Write side for announcements, shared by sync_search.py and import_saved_pages.py.

One post -> chunks (scripts/chunker.py logic) -> e5 "passage:" vectors
(bracu.embedder) -> BM25 postings (bracu/text.py) -> chunks + bm25_postings +
announcements row in ONE transaction, then refresh_bm25_stats(). Not used by
the live site (it only reads these tables).

Rules every source gets:
- chunk ids start at 1,000,000 (corpus ids are 0..n-1, see ingest.py);
- a link-only entry never replaces a full-text import (only last_seen/listed change);
- listed never goes back from true to false;
- an unchanged post (same content hash) is not rewritten.
"""
import hashlib
import json
import re
from collections import Counter
from datetime import date
from urllib.parse import urlsplit, urlunsplit

from chunker import chunk_doc, wc

from bracu.embedder import to_pgvector
from bracu.text import tokenize

ID_START = 1_000_000
LOCK_KEY = 'bracu_announcements'     # one writer at a time (Actions sync vs local import)
CHUNK_COLS = ['id', 'chunk_id', 'doc_id', 'title', 'section', 'status', 'rag_priority',
              'category', 'doc_type', 'source_type', 'url', 'source', 'approved_on',
              'effective_date', 'superseded_by', 'file', 'chunk_index', 'words', 'text',
              'context_text', 'bm25_len', 'embedding', 'origin', 'published_at']


MONTHS = {m: i for i, m in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
_M = (r'\b(?P<m>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|'
      r'Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b\.?')
_D = r'(?P<d>\d{1,2})(?:st|nd|rd|th)?'
_Y = r'(?P<y>20\d\d)'
MDY = _M + r'\s+' + _D + r',?\s+' + _Y           # October 28th, 2026 / Sep 30, 2026
DMY = _D + r'\s+(?:of\s+)?' + _M + r',?\s+' + _Y  # 30th October 2026
WEEKDAY = r'(?:[A-Za-z]+,\s*)?'


def _to_date(m):
    mon = MONTHS.get(m.group('m')[:3].lower())
    try:
        return date(int(m.group('y')), mon, int(m.group('d'))) if mon else None
    except ValueError:
        return None


def find_date(text, patterns=(MDY, DMY), prefix=''):
    """First valid date in `text` matching prefix + one of the patterns."""
    for pat in patterns:
        for m in re.finditer(prefix + pat, text or ''):
            d = _to_date(m)
            if d:
                return d
    return None


def publish_date(text):
    """The page's own "Publish Date: October 28th, 2026 - 9:15am" /
    "Publish Date: Tuesday, October 27, 2026 - 11:30"."""
    return find_date(text, (MDY,), r'Publish(?:ed)?\s+Date:\s*' + WEEKDAY)


def normalize_url(url):
    """https, lower-case host, no query string, fragment or trailing slash."""
    p = urlsplit(url.strip())
    return urlunsplit(('https', p.netloc.lower(), p.path.rstrip('/') or '/', '', ''))


def doc_id_for(url):
    path = urlsplit(normalize_url(url)).path
    slug = re.sub(r'[^a-z0-9]+', '_', path.lower()).strip('_')[:80]
    return f"ann_{slug}_{hashlib.sha1(normalize_url(url).encode()).hexdigest()[:6]}"


def date_label(published_at, date_source):
    if not published_at:
        return 'unknown'
    d = f'{published_at.day} {published_at:%B %Y}'
    return f'around {d}' if date_source == 'found' else d


def build_chunks(ann, text):
    """Chunk dicts (without ids) for one post. `text` is the full post text, or
    the search snippet for a link-only entry."""
    doc_id = doc_id_for(ann['url'])
    full = ann['full_text']
    head = 'Announcement' if full else 'Announcement (link only)'
    header = f"{head}: {ann['title']} | Published: {date_label(ann['published_at'], ann['date_source'])}"
    text = (text or '').strip() or ann['title']
    pieces = chunk_doc({'doc_id': doc_id}, text) if full else [([], text)]
    out = []
    for i, (path, part) in enumerate(pieces):
        sub = ' > '.join(t for _, t in path)
        out.append({
            'chunk_id': f'{doc_id}::{i:03d}', 'doc_id': doc_id, 'title': ann['title'],
            'section': f"{ann['title']} > {sub}" if sub else ann['title'],
            'status': ann.get('status', 'current'),
            'rag_priority': ann.get('rag_priority', 2 if full else 3),
            'category': 'announcement', 'doc_type': 'announcement',
            'source_type': ann['source'], 'url': normalize_url(ann['url']),
            'source': normalize_url(ann['url']), 'approved_on': '',
            'effective_date': ann['published_at'].isoformat() if ann['published_at'] else '',
            'superseded_by': [], 'file': '', 'chunk_index': i, 'words': wc(part),
            'text': part, 'context_text': header + '\n\n' + part,
            'origin': ann['source'], 'published_at': ann['published_at'],
        })
    return out


def content_hash(ann, text):
    key = [ann['title'], str(ann['published_at']), ann['date_source'], ann['full_text'], text]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, conn, embedder=None):
        self.conn = conn
        self._embedder = embedder

    @property
    def embedder(self):
        if self._embedder is None:
            from bracu.embedder import Embedder
            self._embedder = Embedder()
        return self._embedder

    def save(self, ann, text):
        """Insert or update one post. ann: url, title, published_at (date or None),
        date_source, source ('search'|'inbox'), full_text, and optionally snippet,
        listed, pdf_links, note, status, rag_priority.
        Returns 'new', 'updated', 'unchanged' or 'kept' (full text not replaced)."""
        url = normalize_url(ann['url'])
        listed = bool(ann.get('listed'))
        h = content_hash(ann, text)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (LOCK_KEY,))
            cur.execute('SELECT full_text, content_hash FROM announcements WHERE url = %s', (url,))
            row = cur.fetchone()
            if row and ((row[0] and not ann['full_text']) or row[1] == h):
                cur.execute("""UPDATE announcements SET last_seen = now(),
                                   listed = listed OR %s,
                                   listed_at = CASE WHEN %s AND NOT listed THEN now() ELSE listed_at END
                               WHERE url = %s""", (listed, listed, url))
                return 'kept' if row[1] != h else 'unchanged'
            chunks = build_chunks(ann, text)
            vecs = self.embedder.encode([c['context_text'] for c in chunks], kind='passage')
            self._delete_chunks(cur, chunks[0]['doc_id'])
            cur.execute('SELECT COALESCE(MAX(id) + 1, %s) FROM chunks WHERE id >= %s',
                        (ID_START, ID_START))
            next_id = cur.fetchone()[0]
            rows, postings = [], []
            for j, (c, v) in enumerate(zip(chunks, vecs)):
                tf = Counter(tokenize(c['context_text']))
                c.update(id=next_id + j, bm25_len=sum(tf.values()), embedding=to_pgvector(v),
                         superseded_by=json.dumps(c['superseded_by']))
                rows.append([c[k] for k in CHUNK_COLS])
                postings += [(t, c['id'], n) for t, n in tf.items()]
            cast = {'superseded_by': '%s::jsonb', 'embedding': '%s::vector'}
            cur.executemany(f"INSERT INTO chunks ({', '.join(CHUNK_COLS)}) VALUES "
                            f"({', '.join(cast.get(k, '%s') for k in CHUNK_COLS)})", rows)
            cur.executemany('INSERT INTO bm25_postings (term, chunk, tf) VALUES (%s, %s, %s)',
                            postings)
            cur.execute("""
                INSERT INTO announcements (url, title, published_at, date_source, source, snippet,
                    full_text, listed, listed_at, content_hash, pdf_links, note, doc_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s, %s::jsonb, %s, %s)
                ON CONFLICT (url) DO UPDATE SET
                    title = EXCLUDED.title, published_at = EXCLUDED.published_at,
                    date_source = EXCLUDED.date_source, source = EXCLUDED.source,
                    snippet = EXCLUDED.snippet, full_text = EXCLUDED.full_text,
                    listed = announcements.listed OR EXCLUDED.listed,
                    listed_at = COALESCE(announcements.listed_at, EXCLUDED.listed_at),
                    content_hash = EXCLUDED.content_hash, pdf_links = EXCLUDED.pdf_links,
                    note = EXCLUDED.note, last_seen = now()""",
                (url, ann['title'], ann['published_at'], ann['date_source'], ann['source'],
                 ann.get('snippet'), ann['full_text'], listed, listed, h,
                 json.dumps(ann.get('pdf_links') or []), ann.get('note'), chunks[0]['doc_id']))
            cur.execute('SELECT refresh_bm25_stats()')
            return 'updated' if row else 'new'

    def delete(self, url):
        """Remove a post and its chunks (reconciliation of provisional entries)."""
        url = normalize_url(url)
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (LOCK_KEY,))
            cur.execute('DELETE FROM announcements WHERE url = %s RETURNING doc_id', (url,))
            row = cur.fetchone()
            if row:
                self._delete_chunks(cur, row[0])
                cur.execute('SELECT refresh_bm25_stats()')
            return bool(row)

    @staticmethod
    def _delete_chunks(cur, doc_id):
        cur.execute("""DELETE FROM bm25_postings p USING chunks c
                       WHERE c.id = p.chunk AND c.doc_id = %s AND c.origin <> 'corpus'""", (doc_id,))
        cur.execute("DELETE FROM chunks WHERE doc_id = %s AND origin <> 'corpus'", (doc_id,))
