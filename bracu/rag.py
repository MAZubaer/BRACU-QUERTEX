"""Question -> retrieve -> prompt -> LLM (with fallback) -> answer + sources.

Command line (needs DATABASE_URL in .env):
    python -m bracu.rag "What is the fine for smoking on campus?"
"""
import re
import sys

from . import config, llm, query, schedule
from .prompts import build_messages, STATUS_LABEL
from .retriever import Retriever

_retriever = None


def get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


def previous_question(history):
    for m in reversed(history or []):
        if m['role'] == 'user':
            return m['content']
    return ''


BANGLA = re.compile(r'[ঀ-৿]')


def search_query(question):
    """The documents are in English. For a Bangla question, add an English
    translation to the search query (one short extra LLM call) so keyword
    search can match too. The answer is still written in Bangla."""
    if not (config.TRANSLATE_QUERIES and BANGLA.search(question)):
        return question
    try:
        en = llm.complete([
            {'role': 'system', 'content': 'Translate the user question into English for a '
             'search engine about BRAC University. Reply with the translation only.'},
            {'role': 'user', 'content': question}])
        return f'{question}\n{en.strip()[:300]}'
    except llm.AllProvidersFailed:
        return question


def plan_search(question, history=None, use_planner=True, translate=True):
    """What to search for: {"main", "extra", "history_text", "k", "search", "planner"}.
    With the query planner: the question + its standalone form + planned queries.
    Without it (off or failed): the old way (Bangla translation, previous question).
    The synonym query is always added when it matches."""
    p = query.plan(question, history) if use_planner else None
    if p:
        main, history_text = question, ''       # standalone already resolves follow-ups
        extra = [p['standalone'], *p['queries']]
        k = config.PLANNED_TOP_K if len(p['queries']) >= 2 else config.TOP_K
    else:
        main = search_query(question) if translate else question
        history_text, extra, k = previous_question(history), [], config.TOP_K
    syn = query.synonym_query(p['standalone'] if p else question)
    if syn:
        extra.append(syn)
    searched = query.unique([main, *extra])
    return {'main': main, 'extra': searched[1:], 'history_text': history_text, 'k': k,
            'search': searched, 'planner': p['model'] if p else None}


def ask(question, history=None, k=None, include_history=None, retriever=None):
    """Returns (stream, sources, info). Iterate `stream` to get the answer text.
    A pure class-schedule question ("CSE220 section 5") is answered from the schedule
    table with a fixed template: no planner, no search, no LLM call."""
    sched = schedule.lookup(question, history)
    if sched and sched['pure']:
        info = {'search': [], 'planner': None, 'provider': 'schedule', 'model': None}
        return iter([sched['text']]), [sched['card']], info
    r = retriever or get_retriever()
    s = plan_search(question, history)
    sources = r.search(s['main'], k=k or s['k'], include_history=include_history,
                       history_text=s['history_text'], extra_queries=s['extra'])
    if sched:                                   # schedule + something else: the card goes first
        sources.insert(0, sched['card'])
    messages = build_messages(question, sources, history)
    info = {'search': s['search'], 'planner': s['planner']}
    return llm.stream_chat(messages, info), sources, info


# One citation group: [S2], [ S2 ], 【S2】, and also ranges and lists the model sometimes
# writes despite the prompt: [S1-S4], [S1–4], [S1, S2], [S1, S3-S5], [S1 and S2].
# public/index.html renders citations with the same rules.
CITE_GROUP = re.compile(r'[\[【]\s*(S\s*\d+(?:\s*(?:[-–—,]|and)\s*S?\s*\d+)*)\s*[\]】]', re.I)
CITE_PART = re.compile(r'(\d+)(?:\s*[-–—]\s*S?\s*(\d+))?', re.I)
MAX_RANGE = 20                                   # a longer "range" is a typo: keep its two ends


def citation_numbers(group):
    """'S1-S4' -> [1, 2, 3, 4]; 'S1, S3' -> [1, 3] (in order, no repeats)."""
    out = []
    for a, b in CITE_PART.findall(group):
        a, b = int(a), int(b or a)
        out += list(range(a, b + 1)) if 0 <= b - a <= MAX_RANGE else [a, b]
    return list(dict.fromkeys(out))


def cited_numbers(answer):
    """Source numbers the answer cites, sorted ([S1-S3] counts S1, S2 and S3)."""
    return sorted({n for g in CITE_GROUP.findall(answer) for n in citation_numbers(g)})


def plain_excerpt(text, limit=400):
    """The passage as plain text for the page: no markdown heading marks, bold or code ticks."""
    t = re.sub(r'(?m)^\s*#{1,6}\s*', '', text)
    t = re.sub(r'[*`]+|(?<!\w)_(?!\w)', '', t)
    return ' '.join(t.split())[:limit]


def source_card(n, c):
    """What the web page shows for source [Sn] (no passage text by default)."""
    return {
        'n': n, 'chunk_id': c['chunk_id'], 'doc_id': c['doc_id'],
        'title': c['title'], 'section': c['section'], 'status': c['status'],
        'status_label': STATUS_LABEL.get(c['status'], c['status']),
        'approved_on': c.get('approved_on') or '', 'url': c.get('url') or '',
        'excerpt': plain_excerpt(c['text']),
        'added_reason': c.get('added_reason', ''),
        **({'published': c['published_at'].isoformat() if c.get('published_at') else '',
            'date_source': c.get('date_source') or '', 'link_only': not c.get('full_text'),
            'listed': bool(c.get('listed'))} if c.get('doc_type') == 'announcement' else {}),
    }


def main():
    q = ' '.join(sys.argv[1:]).strip() or input('Question: ').strip()
    print(f'Search mode: {get_retriever().mode}')
    stream, sources, info = ask(q)
    answer = ''
    try:
        for piece in stream:
            answer += piece
            print(piece, end='', flush=True)
    except llm.AllProvidersFailed as e:
        print(f'[No LLM available: {e}]')
    print(f"\n\n({info.get('provider', '-')}/{info.get('model', '-')})")
    for n in cited_numbers(answer) or range(1, len(sources) + 1):
        if 1 <= n <= len(sources):
            c = sources[n - 1]
            print(f"  [S{n}] {c['title']} — {c['section']} "
                  f"({STATUS_LABEL.get(c['status'], c['status'])}) {c['url'] or c['file']}")


if __name__ == '__main__':
    main()
