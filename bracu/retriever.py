"""Hybrid search in Postgres: BM25 keywords (SQL over bm25_postings) +
pgvector similarity, fused with Reciprocal Rank Fusion, then re-ranked by
document status. Same rules as the original in-memory retriever.py:

- current, approved rules rank first; drafts allowed but down-weighted;
- superseded (history-only) documents are left out unless the question
  asks about history;
- the #1 keyword hit, #1 vector hit and best raw matches always get a place;
- at most MAX_PER_DOC passages per document, MAX_CONTEXT_WORDS in total;
- if a non-current document is used, the matching part of the Current
  Rules Guide is added so the model sees which document governs.

Extra queries (from the query planner / synonym list) each add their own
keyword and vector lists; all lists are fused together, then the same rules
apply. Without extra queries the results are exactly as before.

Per question: 1 query for all candidate lists (ids + light metadata),
1 query for the full text of the chosen passages (+1 rarely, for the guide).
"""
import re
from collections import Counter, defaultdict
from datetime import timedelta

from . import config, db
from .embedder import get_embedder, to_pgvector
from .text import tokenize

PRIORITY_WEIGHT = {1: 1.15, 2: 1.0, 3: 0.8, 4: 0.5, 0: 0.0}
HISTORY_WORDS = re.compile(
    r'\b(history|historical|old|older|previous|previously|earlier|former|past|'
    r'superseded|replaced|2010|used to)\b|আগে|পুরনো|পুরাতন', re.I)
RRF_K = 60
K1, B = 1.5, 0.75
GUIDE_DOC = 'current_rules_guide'

CHUNK_COLUMNS = ('id, chunk_id, doc_id, title, section, status, rag_priority, category, '
                 'doc_type, source_type, url, source, approved_on, effective_date, '
                 'superseded_by, file, chunk_index, words, text, context_text, origin, published_at')
ANNOUNCEMENT_COLUMNS = ('date_source', 'full_text', 'listed')   # from the announcements table

# Announcements (doc_type 'announcement') rank by age; corpus documents are unchanged.
# Older than a year they are left out like superseded documents (unless history).
RECENT_DAYS, OLD_DAYS = 90, 365
RECENT_WEIGHT, AGING_WEIGHT, UNCONFIRMED_WEIGHT = 1.1, 0.8, 0.9
# "Latest notices" questions also get the newest announcements by date.
NOTICE_WORDS = re.compile(r'\b(notices?|announcements?|circulars?)\b|নোটিশ|ঘোষণা|বিজ্ঞপ্তি', re.I)
RECENT_WORDS = re.compile(r'\b(latest|recent|recently|new|newest|today|this\s+week|upcoming)\b|'
                          r'সর্বশেষ|নতুন|সাম্প্রতিক|আজ', re.I)
LATEST_EXTRA_WORDS = 1500        # word budget for those extra announcements

# BM25 score of every chunk containing at least one query term.
BM25_SQL = f"""
    SELECT p.chunk AS id,
           SUM(t.idf * p.tf * {K1 + 1} /
               (p.tf + {K1} * ({1 - B} + {B} * c.bm25_len / m.avg_len))) AS score
    FROM bm25_postings p
    JOIN bm25_terms t ON t.term = p.term
    JOIN chunks c ON c.id = p.chunk
    CROSS JOIN (SELECT (value->>'avg_len')::float8 AS avg_len
                FROM index_info WHERE key = 'bm25') m
    WHERE p.term = ANY(%({{terms}})s) {{extra}}
    GROUP BY p.chunk"""

LIGHT = 'c.doc_id, c.status, c.rag_priority, c.words, c.doc_type, c.published_at, a.listed'
LIGHT_JOIN = 'LEFT JOIN announcements a ON a.doc_id = c.doc_id'


def same_text_key(text):
    """Queries that differ only in case, spacing or punctuation are one query."""
    return ' '.join(re.findall(r'\w+', text.lower()))


def wants_latest(texts):
    """A question about the latest / recent / new notices or announcements."""
    return any(NOTICE_WORDS.search(t) and RECENT_WORDS.search(t) for t in texts if t)


def announcement_weight(c, today):
    """(weight, too_old) for an announcement chunk: x1.1 under 90 days, x0.8 up to
    a year, older = like superseded; x0.9 more while not confirmed on the
    announcements listing (listed=false)."""
    age = (today - c['published_at']).days if c['published_at'] else 0
    if age > OLD_DAYS:
        return 1.0, True
    w = RECENT_WEIGHT if age < RECENT_DAYS else AGING_WEIGHT
    return (w * UNCONFIRMED_WEIGHT if c['listed'] is False else w), False


class Retriever:
    def __init__(self, embedder='auto'):
        # embedder: 'auto' = load the ONNX model (None if unavailable);
        # None = keyword-only; or any object with .encode(texts, kind=...).
        self._embedder = embedder

    @property
    def embedder(self):
        return get_embedder() if self._embedder == 'auto' else self._embedder

    @property
    def mode(self):
        return 'hybrid' if self.embedder is not None else 'keyword-only'

    # ---------------------------------------------------------------- SQL
    def _candidates(self, queries, n):
        """For each query: [bm25 ids], [vector ids] best first (one flat list of
        lists, in query order), and id -> light metadata."""
        emb = self.embedder
        parts, params = [], {'n': n}
        # all queries in one encode() call
        vecs = emb.encode(list(queries), kind='query') if emb is not None else []
        for j, q in enumerate(queries):
            params[f'terms{j}'] = terms = sorted(set(tokenize(q)))
            if terms:
                parts.append(f"""
                    SELECT 'bm{j}' AS kind, bm.id, bm.score, {LIGHT}
                    FROM ({BM25_SQL.format(terms=f'terms{j}', extra='')}
                          ORDER BY score DESC, id LIMIT %(n)s) bm
                    JOIN chunks c ON c.id = bm.id {LIGHT_JOIN}""")
            if emb is not None:
                params[f'qv{j}'] = to_pgvector(vecs[j])
                parts.append(f"""
                    SELECT 'vec{j}' AS kind, v.id, v.score, {LIGHT}
                    FROM (SELECT id, 1 - (embedding <=> %(qv{j})s::vector) AS score
                          FROM chunks WHERE embedding IS NOT NULL
                          ORDER BY embedding <=> %(qv{j})s::vector, id LIMIT %(n)s) v
                    JOIN chunks c ON c.id = v.id {LIGHT_JOIN}""")
        if not parts:
            return [], {}
        rows = db.fetchall(' UNION ALL '.join(f'({p})' for p in parts), params)
        lists = defaultdict(list)
        meta = {}
        for kind, i, score, doc_id, status, prio, words, doc_type, published_at, listed in rows:
            lists[kind].append((score, i))
            meta[i] = {'doc_id': doc_id, 'status': status, 'rag_priority': prio, 'words': words,
                       'doc_type': doc_type, 'published_at': published_at, 'listed': listed}
        out = []
        for j in range(len(queries)):
            out.append([i for _, i in sorted(lists[f'bm{j}'], key=lambda x: (-x[0], x[1]))])
            if emb is not None:
                out.append([i for _, i in sorted(lists[f'vec{j}'], key=lambda x: (-x[0], x[1]))])
        return out, meta

    @staticmethod
    def _best_guide_by_keywords(q):
        """Best Current Rules Guide chunk by BM25 alone (0 if no word matches)."""
        row = db.fetchone(f"""
            SELECT c.id FROM chunks c
            LEFT JOIN ({BM25_SQL.format(terms='terms',
                                        extra="AND c.doc_id = '" + GUIDE_DOC + "'")}) s
                   ON s.id = c.id
            WHERE c.doc_id = %(doc)s
            ORDER BY COALESCE(s.score, 0) DESC, c.id DESC LIMIT 1""",
                          {'terms': sorted(set(tokenize(q))), 'doc': GUIDE_DOC})
        return row[0] if row else None

    @staticmethod
    def _fetch(ids):
        if not ids:
            return {}
        cols = [c.strip() for c in CHUNK_COLUMNS.split(',')]
        rows = db.fetchall(f"""SELECT {', '.join('c.' + k for k in cols)},
                                      {', '.join('a.' + k for k in ANNOUNCEMENT_COLUMNS)}
                               FROM chunks c {LIGHT_JOIN} WHERE c.id = ANY(%s)""", (list(ids),))
        cols += ANNOUNCEMENT_COLUMNS
        return {r[0]: dict(zip(cols, r)) for r in rows}

    @staticmethod
    def _latest_announcements(today, include_history, n):
        """First chunk of the n newest announcements: [(id, words)]."""
        return db.fetchall("""
            SELECT c.id, c.words FROM announcements a
            JOIN chunks c ON c.doc_id = a.doc_id AND c.chunk_index = 0
            WHERE %(hist)s OR a.published_at IS NULL OR a.published_at >= %(since)s
            ORDER BY a.published_at DESC NULLS LAST, a.listed DESC, c.id
            LIMIT %(n)s""", {'hist': include_history, 'n': n,
                             'since': today - timedelta(days=OLD_DAYS)})

    # ---------------------------------------------------------------- search
    def search(self, query, k=None, include_history=None, history_text='', extra_queries=()):
        """Return the top-k chunks (dicts with an added 'score').
        extra_queries: more phrasings of the same question, searched too and fused."""
        k = k or config.TOP_K
        # Short follow-ups ("and the fine?") borrow words from the previous question.
        q = query if len(query.split()) > 6 or not history_text else f'{history_text} {query}'
        queries = [q]
        for e in extra_queries:
            if e and same_text_key(e) not in {same_text_key(x) for x in queries}:
                queries.append(e)
        if include_history is None:
            include_history = any(HISTORY_WORDS.search(x) for x in [query, *queries[1:]])

        lists, meta = self._candidates(queries, config.CANDIDATES)
        fused = defaultdict(float)
        for lst in lists:
            for rank, i in enumerate(lst):
                fused[i] += 1.0 / (RRF_K + rank + 1)

        today = config.today()
        scored, raw = [], []
        for i, s in fused.items():
            c = meta[i]
            aw, too_old = announcement_weight(c, today) if c['doc_type'] == 'announcement' else (1.0, False)
            if (c['status'] == 'superseded' or too_old) and not include_history:
                continue
            w = PRIORITY_WEIGHT.get(int(c['rag_priority']), 1.0) * aw
            if c['status'] == 'superseded' or too_old:
                w = 1.0                  # the question is about history: treat normally
            scored.append((s * w, i))
            raw.append((s, i))
        scored.sort(reverse=True)
        raw.sort(reverse=True)

        # Best raw matches always get a place (even drafts, so the model can
        # say "only a draft covers this"); so do the #1 keyword and #1 vector
        # match of the question, and for each extra query (one fact each) its
        # best match by its own keyword + vector lists. (Not every list's #1:
        # a short query's vector #1 is often a near miss, e.g. the ECE page
        # for "EEE semester fee".) The rest go by status-weighted score.
        weighted = {i: s for s, i in scored}
        per = len(lists) // len(queries)        # lists per query: keyword (+ vector)
        must = [lst[0] for lst in lists[:per] if lst and lst[0] in weighted]
        for j in range(per, len(lists), per):
            own = defaultdict(float)
            for lst in lists[j:j + per]:
                for rank, i in enumerate(lst):
                    if i in weighted:
                        own[i] += 1.0 / (RRF_K + rank + 1)
            if own:
                must.append(max(own, key=lambda i: (own[i], -i)))
        must = list(dict.fromkeys(must))
        for s, i in raw[:3]:
            if meta[i]['doc_id'] not in {meta[j]['doc_id'] for j in must}:
                must.append(i)
        order = sorted(must, key=lambda i: -weighted[i]) + [i for _, i in scored if i not in must]

        chosen, per_doc, words = [], Counter(), 0
        for i in order:
            c = meta[i]
            if per_doc[c['doc_id']] >= config.MAX_PER_DOC:
                continue
            if chosen and words + c['words'] > config.MAX_CONTEXT_WORDS:
                continue
            chosen.append(i)
            per_doc[c['doc_id']] += 1
            words += c['words']
            if len(chosen) >= k:
                break

        # Add the Current Rules Guide if a non-current document made the list.
        guide = None
        if any(meta[i]['status'] != 'current' for i in chosen) and \
                not any(meta[i]['doc_id'] == GUIDE_DOC for i in chosen):
            g = [(fused[i], i) for i in fused if meta[i]['doc_id'] == GUIDE_DOC and fused[i] > 0]
            guide = max(g)[1] if g else self._best_guide_by_keywords(' '.join(queries))

        # "What are the latest notices?": add the newest announcements by date.
        latest = []
        latest_mode = wants_latest([query, *queries])
        if latest_mode:
            extra_words = 0
            for i, w in self._latest_announcements(today, include_history, config.LATEST_ANNOUNCEMENTS):
                if i not in chosen and extra_words + w <= LATEST_EXTRA_WORDS:
                    latest.append(i)
                    extra_words += w

        rows = self._fetch(chosen + latest + ([guide] if guide is not None else []))
        out = [{**rows[i], 'score': round(weighted[i], 5)} for i in chosen if i in rows]
        out.sort(key=lambda c: -c['score'])
        out += [{**rows[i], 'score': 0.0, 'added_reason': 'latest announcements'}
                for i in latest if i in rows]
        if latest_mode:
            # every announcement first, newest first: they are what the question asks
            # for; the other passages keep their order after them (stable sort)
            out.sort(key=lambda c: (c['doc_type'] != 'announcement',
                                    -c['published_at'].toordinal() if c.get('published_at') else 0))
        if guide is not None and guide in rows:
            out.append({**rows[guide], 'score': 0.0, 'added_reason': 'rules guide'})
        return out


def latest_announcements(limit=5):
    """For /api/announcements and the "Latest notices" panel: posts confirmed on
    the announcements listing first, then provisional (search) ones; last year only."""
    rows = db.fetchall("""
        SELECT title, published_at, date_source, url, full_text, listed FROM announcements
        WHERE published_at IS NULL OR published_at >= %s
        ORDER BY listed DESC, published_at DESC NULLS LAST, url LIMIT %s""",
                       (config.today() - timedelta(days=OLD_DAYS), limit))
    return [{'title': t, 'date': d.isoformat() if d else None, 'date_source': ds, 'url': u,
             'full_text': ft, 'listed': li} for t, d, ds, u, ft, li in rows]


def index_info():
    """Facts about the loaded index, for /api/health."""
    rows = db.fetchall("SELECT key, value FROM index_info")
    info = {k: v for k, v in rows}
    info['chunks'] = db.fetchone('SELECT count(*) FROM chunks')[0]
    return info
