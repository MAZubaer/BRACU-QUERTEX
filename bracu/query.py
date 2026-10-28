"""Think first, then search.

Students use different words than the documents ("how much do I spend" vs
"tuition fee per credit"), and some answers need facts from several documents.

- plan(): one fast LLM call rewrites the question as a standalone English
  question plus 1-4 short queries in the documents' wording, one per fact.
- synonym_query(): a free word list that adds document words to student
  words. Used for questions only, at search time; bracu/text.py (which built
  the BM25 postings in Postgres) is not changed.
"""
import json
import re

from . import config, llm
from .retriever import same_text_key
from .text import tokenize

PLANNER_PROMPT = """You prepare search queries for a search engine over BRAC University \
documents. The documents are formal English BRAC University policies, fee tables and \
university web pages. Students often use different, informal words, and some questions \
need several facts from different documents.

Reply with JSON only, no other text:
{"standalone": "...", "queries": ["...", ...]}

"standalone": the user's latest question rewritten as one complete English question. If it \
is a follow-up (e.g. "and the fine?", "what about EEE?"), fill in the missing parts from the \
conversation. If it is in Bangla, translate it into English.

"queries": 1 to 4 short search queries written in the DOCUMENTS' wording, one per fact \
needed to answer. Use formal terms such as "tuition fee per credit", "semester fee", \
"disciplinary penalty", "make-up examination", "Residential Semester". Keep program codes \
and names (CSE, EEE, BBA, LLB, SHEBREC) exactly. Example: "total cost to graduate in CSE" -> \
["CSE tuition fee per credit", "total credits required for BSc CSE", "CSE semester fee", \
"residential semester fee"]. A simple question needs only one query.

Never answer the question yourself and never invent facts or numbers."""

# student words -> words the documents use
SYNONYMS = [
    (('expense', 'cost', 'price', 'how much', 'money', 'spend', 'afford', 'budget', 'charges'),
     'fee fees tuition'),
    (('fine', 'penalty', 'punishment'), 'fine penalty'),
    (('kicked out', 'expelled'), 'expulsion suspension disciplinary'),
    (('cheating', 'copying', 'plagiarism'), 'academic misconduct plagiarism'),
    (('exam', 'test', 'midterm', 'final'), 'examination'),
    (('scholarship', 'waiver', 'discount', 'financial aid'), 'scholarship waiver financial aid'),
    (('drop', 'withdraw'), 'drop withdrawal'),
    (('bus', 'shuttle', 'transport'), 'transport bus'),
    (('hostel', 'dorm', 'savar'), 'residential semester'),
    (('gpa', 'cgpa', 'grades'), 'grading cgpa'),
    (('graduate', 'graduation'), 'degree credits'),
    (('doctor', 'sick', 'medical'), 'medical center'),
    (('harass', 'bully'), 'harassment'),
    (('late', 'attendance', 'absent'), 'attendance'),
]


def _pattern(words):
    # whole words, case-insensitive; a plural "s" is allowed (costs, exams, fines)
    alts = [re.escape(w).replace(r'\ ', r'\s+') + ('' if ' ' in w else 's?') for w in words]
    return re.compile(r'\b(?:' + '|'.join(alts) + r')\b', re.I)


SYNONYM_RULES = [(_pattern(words), add.split()) for words, add in SYNONYMS]


def synonym_query(text):
    """`text` plus document words for the student words in it, or None if
    nothing matched. Words already present (same stem) are not added again."""
    present = set(tokenize(text))
    added = []
    for pattern, words in SYNONYM_RULES:
        if not pattern.search(text):
            continue
        for w in words:
            stem = tokenize(w)[0]
            if stem not in present:
                present.add(stem)
                added.append(w)
    return f"{text} {' '.join(added)}" if added else None


def unique(texts, limit=None):
    out, seen = [], set()
    for t in texts:
        key = same_text_key(t)
        if key and key not in seen:
            seen.add(key)
            out.append(t)
    return out[:limit] if limit else out


def _parse(reply):
    """First {...} block of the reply -> (standalone, queries). Raises ValueError."""
    start = reply.find('{')
    if start < 0:
        raise ValueError('no JSON object')
    data, _ = json.JSONDecoder().raw_decode(reply, start)
    if not isinstance(data, dict):
        raise ValueError('not a JSON object')
    standalone = data.get('standalone')
    standalone = ' '.join(standalone.split())[:300] if isinstance(standalone, str) else ''
    queries = data.get('queries')
    queries = [' '.join(q.split())[:200] for q in queries if isinstance(q, str)] \
        if isinstance(queries, list) else []
    queries = unique(queries, config.MAX_PLANNED_QUERIES)
    if not standalone and not queries:
        raise ValueError('empty plan')
    return standalone, queries


def plan(question, history=None):
    """{"standalone", "queries", "model"} for `question`, or None if the planner
    is switched off or fails (the caller then searches the old way)."""
    if not config.QUERY_PLANNER:
        return None
    convo = '\n'.join(f"{m['role']}: {' '.join(m['content'].split())[:600]}"
                      for m in (history or [])[-4:])
    user = (f'CONVERSATION SO FAR:\n{convo}\n\n' if convo else '') + f'LATEST QUESTION: {question}'
    info = {}
    try:
        reply = llm.complete([{'role': 'system', 'content': PLANNER_PROMPT},
                              {'role': 'user', 'content': user}],
                             info, chain=config.PLANNER_CHAIN, max_tokens=300)
        if info.get('provider') == 'mock':
            raise ValueError('mock provider cannot plan')
        standalone, queries = _parse(reply)
    except llm.AllProvidersFailed as e:
        print(f'[planner] skipped: {e}')
        return None
    except ValueError as e:
        print(f"[planner] skipped: unusable reply from {info.get('model', '-')} ({e})")
        return None
    return {'standalone': standalone or question, 'queries': queries, 'model': info.get('model')}
