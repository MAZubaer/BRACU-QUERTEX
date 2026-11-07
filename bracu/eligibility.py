"""Prerequisite eligibility check, computed from the course catalogue (no LLM).

    "I've finished CSE 110, 111, 220 and 221. What can I take next?"     -> next
    "Can I take CSE 420 if I've done CSE 221 but not CSE 331?"            -> check
    "What do I need before CSE 340?"                                      -> lookup

card_for(question) returns a source card (a chunk-like dict) that the retriever puts
first in the sources, or None when the question isn't about prerequisites. For "next",
the program's courses are grouped: (a) can take now, (b) no prerequisite listed,
(c) needs permission, (d) not yet (with the missing courses), (e) prerequisite written
in words (original text). Only courses listed in the program's own documents are used.

The data (programs, course titles, prerequisite rules) comes from the course_catalog
table, written by scripts/build_course_catalog.py --load-db; the rule format is
described at parse_prereq() there.
"""
import re
from collections import Counter

from . import db

DOC_ID = 'eligibility_check'
_data = None


def load():
    """The eligibility data from Postgres (once per server instance)."""
    global _data
    if _data is None:
        row = db.fetchone("SELECT value FROM course_catalog WHERE key = 'eligibility'")
        _data = row[0] if row else {}
    return _data


# ---------------------------------------------------------------- reading the question
CODE_TOKEN = re.compile(r'(?<![A-Za-z0-9])(?:([A-Za-z]{2,4})\s?-?\s?)?(\d{3}[A-Za-z]?)(?![A-Za-z0-9])')
LIST_GAP = re.compile(r'(?:\s|,|/|&|\band\b|\bor\b)*', re.I)
SENTENCE_END = re.compile(r'[.?!;]\s|[.?!;]$|\n')
DONE = re.compile(r"\b(?:finished|completed|complete|done|taken|took|passed|cleared|did|studied|after)\b", re.I)
NEGATION = re.compile(r"\b(?:not|never|without|except|excluding|yet\s+to|cannot)\b|n['’]t\b", re.I)
WANT = re.compile(r"\b(?:take|taking|enrol+(?:ing)?|register(?:ing)?|before|eligible|"
                  r"prerequisites?|pre-?\s?reqs?|requirements?|need(?:ed)?|required)\b", re.I)
NEXT = re.compile(r"\bnext\b|\bwhat\s+(?:other\s+|more\s+|else\s+)?(?:courses?\s+|classes\s+)?"
                  r"(?:can|could|should|may)\s+i\s+(?:take|do|register|enrol)|"
                  r"\bwhich\s+(?:courses?|classes)\b.*\b(?:can|could|should|may)\s+i\b|"
                  r"\beligible\b|\bunlock|\b(?:available|open)\s+(?:to|for)\s+me\b", re.I)
PREREQ_ASK = re.compile(r"pre-?\s?req|prerequisite|\bneed\b.*\b(?:before|for|to\s+take)\b|"
                        r"\brequired\s+(?:before|for)\b|\bcan\s+i\s+(?:take|do|enrol|register)\b|"
                        r"\beligible\b|\ballowed\s+to\s+take\b|\brequirements?\s+(?:for|of)\b|পূর্বশর্ত", re.I)


def find_codes(text, prefixes):
    """[(code, start)] in text. A bare number continues a list ("CSE 110, 111 and 221")."""
    out, last, last_end = [], None, None
    for m in CODE_TOKEN.finditer(text):
        p, num = (m.group(1) or '').upper(), m.group(2)
        start = m.start() if p in prefixes else m.start(2)
        if p not in prefixes:
            if not last or not LIST_GAP.fullmatch(text[last_end:m.start(2)]):
                continue
            p = last
        out.append((f'{p} {num[:3]}{num[3:].upper()}', start))
        last, last_end = p, m.end()
    return out


def roles(question, prefixes):
    """{'done': [...], 'not': [...], 'want': [...], 'free': [...]} by the nearest cue word
    before each code in its sentence ("finished", "not", "take"), else the next one after it."""
    cues = []
    for kind, rx in (('done', DONE), ('want', WANT), ('not', NEGATION)):
        for m in rx.finditer(question):
            cues.append((m.start(), m.end(), kind))
    cues.sort()
    # "haven't done", "not taken": a negated completion
    cues = [(s, e, 'not' if k == 'done' and NEGATION.search(question[max(0, s - 12):s]) else k)
            for s, e, k in cues]
    ends = [m.end() for m in SENTENCE_END.finditer(question)]
    out = {'done': [], 'not': [], 'want': [], 'free': []}
    for code, pos in find_codes(question, prefixes):
        lo = max([e for e in ends if e <= pos], default=0)
        hi = min([e for e in ends if e > pos], default=len(question))
        before = [k for s, e, k in cues if lo <= s and e <= pos]
        after = [k for s, e, k in cues if pos < s and e <= hi]
        kind = before[-1] if before else (after[0] if after and after[0] in ('done', 'not') else 'free')
        if code not in out[kind]:
            out[kind].append(code)
    return out


def detect_program(question, codes, programs):
    """(key, how, candidates): the program named in the question, else the one the
    course codes point to; key None when it is unclear."""
    text = re.sub(r'(?<![A-Za-z0-9])[A-Za-z]{2,4}\s?-?\s?\d{3}[A-Za-z]?(?![A-Za-z0-9])', ' ', question)
    low = text.lower().replace('&', 'and')
    spans = []
    for key, p in programs.items():
        for m in re.finditer(r'(?<![A-Za-z])' + re.escape(key) + r'(?![A-Za-z])', text):
            spans.append((m.start(), m.end(), key))
        for name in p['names']:
            n = name.lower().replace('&', 'and')
            one_word = len(n.split()) == 1
            pat = (r'\b' + re.escape(n) + r'\s+(?:program(?:me)?|major|student|degree|department|dept)\b'
                   if one_word else r'\b' + re.escape(n) + r'\b')
            for m in re.finditer(pat, low):
                spans.append((m.start(), m.end(), key))
    keys = {k for s, e, k in spans
            if not any(s2 <= s and e <= e2 and (s2, e2) != (s, e) for s2, e2, _ in spans)}
    if len(keys) == 1:
        return keys.pop(), 'question', []
    if keys:
        return None, 'question', sorted(keys)
    if not codes:
        return None, 'codes', []
    prefix, n = Counter(c.split()[0] for c in codes).most_common(1)[0]
    if n * 2 <= len(codes):
        return None, 'codes', []
    nums = sorted(int(c.split()[1][:3]) for c in codes if c.split()[0] == prefix)
    level = 'graduate' if nums[len(nums) // 2] >= 500 else 'undergraduate'
    cands = sorted(k for k, p in programs.items() if p['prefix'] == prefix and p['level'] == level)
    if len(cands) > 1 and prefix in cands:
        return prefix, 'codes', [c for c in cands if c != prefix]      # CSE codes -> the CSE program
    if len(cands) == 1:
        return cands[0], 'codes', []
    return None, 'codes', cands


# ---------------------------------------------------------------- evaluating rules
ORDER = {'yes': 0, 'perm': 1, 'unknown': 2, 'no': 3}


def describe(node):
    if isinstance(node, str):
        return 'permission of the instructor' if node == 'PERMISSION' else node
    if 'text' in node:
        return node['text']
    kind, items = next(iter(node.items()))
    parts = [describe(x) if isinstance(x, str) or 'text' in x else f'({describe(x)})' for x in items]
    return (' or ' if kind == 'any_of' else ' and ').join(parts)


def evaluate(node, done):
    """(state, unmet): state 'yes' | 'perm' (only the instructor's permission is left) |
    'unknown' (a condition in words) | 'no'; unmet: what is not met yet, in words."""
    if isinstance(node, str):
        if node == 'PERMISSION':
            return 'perm', [describe(node)]
        return ('yes', []) if node in done else ('no', [node])
    if 'text' in node:
        return 'unknown', [node['text']]
    kind, items = next(iter(node.items()))
    res = [evaluate(x, done) for x in items]
    states = [s for s, _ in res]
    if kind == 'any_of':
        for s in ('yes', 'perm', 'unknown'):
            if s in states:
                return s, ([] if s == 'yes' else [describe(node)])
        return 'no', [describe(node)]
    worst = max(states, key=ORDER.get)
    return worst, [u for s, us in res if s != 'yes' for u in us]


def evaluate_rule(rule, done):
    if rule['status'] == 'none':
        return 'none', []
    if rule['status'] == 'unparsed':
        return 'unknown', [rule['text']]
    state, unmet = evaluate({'all_of': rule['all_of']}, done)
    if state == 'no' and rule.get('or_equivalent'):
        unmet = [*unmet, 'or an equivalent course']
    return state, unmet


def course_entries(code, data, program):
    """The course's prerequisite entries, from the program's documents when they give one.
    Grouped by offering: one code can name two courses (CSE 490)."""
    c = data['courses'].get(code) or {}
    pres = c.get('prereqs', [])
    own = [p for p in pres if program and set(p['docs']) & set(program['docs'])]
    groups = {}
    for p in own or pres:
        groups.setdefault(p.get('offering'), []).append(p)
    return groups


def label(doc_ids, data, program):
    docs = [d for d in doc_ids if not program or d in program['docs']] or doc_ids
    return '; '.join(data['docs'].get(d, {}).get('label', d) for d in docs)


def judge(code, offering, entries, done, data, program):
    """One course (or one offering of a shared code) -> (group, line)."""
    title = offering or (data['courses'].get(code) or {}).get('title') or ''
    tag = (program or {}).get('courses', {}).get(code, '')
    head = f"{code} {title}".strip() + (f' ({tag})' if tag else '')
    if not entries:
        return 'b', head
    results = [(p, *evaluate_rule(p['rule'], done)) for p in entries]
    state = max((s for _, s, _ in results), key=lambda s: ORDER.get(s, -1))
    texts = '; '.join(f"{p['text']} [{label(p['docs'], data, program)}]" for p, _, _ in results)
    unmet = list(dict.fromkeys(u for _, s, us in results if s != 'yes' for u in us))
    if state == 'none':
        return 'b', f"{head}. Prerequisite: None [{label(entries[0]['docs'], data, program)}]"
    if state == 'yes':
        return 'a', f'{head}. Prerequisite: {texts}'
    if state == 'perm':
        return 'c', f"{head}. Prerequisite: {texts}. Needs: {'; '.join(unmet)}"
    if state == 'unknown':
        return 'e', f'{head}. Prerequisite as written: "' + '"; "'.join(p['text'] for p in entries) + \
            f'" [{label(entries[0]["docs"], data, program)}]'
    return 'd', f"{head}. Prerequisite: {texts}. Missing: {'; '.join(unmet)}"


GROUPS = [('a', 'Can take now (every listed prerequisite is completed)'),
          ('b', 'No prerequisite listed in the documents'),
          ('c', "Needs the instructor's permission (the listed courses are not all completed; "
                "the documents allow permission of the instructor instead)"),
          ('d', 'Not yet: prerequisites missing'),
          ('e', 'Prerequisite written in words: not checked automatically (original text)')]


# ---------------------------------------------------------------- the card
def card_for(question, data=None):
    """The computed eligibility card for this question, or None."""
    try:
        data = data if data is not None else load()
    except Exception as e:                  # no table yet, database down: no card
        print(f'[eligibility] {type(e).__name__}: {e}')
        return None
    if not data or not data.get('programs'):
        return None
    prefixes = {c.split()[0] for c in data['courses']}
    r = roles(question, prefixes)
    done = set(r['done'])
    targets = [c for c in r['want'] if c not in done]
    if targets and PREREQ_ASK.search(question):
        mode = 'check' if (done or r['not']) else 'lookup'
    elif not targets and NEXT.search(question) and (done or r['free']):
        mode, done = 'next', done | set(r['free'])
    else:
        return None
    codes = sorted(done | set(targets) | set(r['not']))
    key, how, cands = detect_program(question, codes, data['programs'])
    program = data['programs'].get(key) if key else None
    if mode == 'next' and not program:
        return unclear_card(sorted(done), cands, how, data)
    lines = []
    if program:
        docs = [data['docs'].get(d, {}) for d in program['docs']]
        based = '; '.join(f"{d.get('label')} ({d.get('title')})" for d in docs)
        lines.append(f"Program: {program['name']} ({program['key']}). Computed by the app (not by the AI) "
                     f"from the course lists and prerequisites in: {based}.")
        if how == 'codes':
            lines.append(f"The program was taken from the course codes in the question ({program['key']})"
                         + (f"; other programs use these codes too ({', '.join(cands)}), so the result "
                            f"may differ for them." if cands else '.'))
    else:
        lines.append('Prerequisite check computed by the app (not by the AI) from the course pages.')
    if done:
        lines.append(f"Completed (as given in the question): {', '.join(sorted(done))}.")
    if r['not']:
        lines.append(f"Not completed (as given in the question): {', '.join(r['not'])}.")
    if mode == 'next':
        lines += next_lines(done, data, program)
    else:
        for code in targets:
            lines += target_lines(code, done, data, program, mode)
    return make_card(lines, program, data, targets, mode)


def next_lines(done, data, program):
    eqs = {e for c in done for e in (data['courses'].get(c) or {}).get('equivalents', [])}
    pfx = {program['prefix']} | {c.split()[0] for c in done}
    todo = [c for c in program['courses'] if c.split()[0] in pfx and c not in done and c not in eqs]
    found = {g: [] for g, _ in GROUPS}
    for code in todo:
        for offering, entries in (course_entries(code, data, program) or {None: []}).items():
            g, line = judge(code, offering, entries, done, data, program)
            found[g].append(line)
    out = [f"Checked: the {', '.join(sorted(pfx))} courses in this program's structure ({len(todo)}), "
           f"except the completed ones."]
    for g, title in GROUPS:
        if found[g]:
            out += ['', f'({g}) {title}:'] + [f'- {x}' for x in found[g]]
    # "Missing: MAT 120" for a student who only listed CSE courses: maybe just not mentioned
    other = sorted({c for x in found['d'] for c in re.findall(r'\b[A-Z]{2,4} \d{3}[A-Z]?\b', x.split('Missing:')[-1])
                    if c.split()[0] not in {d.split()[0] for d in done}})
    if other:
        out += ['', f"Not mentioned in the question: {', '.join(other)}. If the student has completed "
                    f"them, more courses in (d) can be taken."]
    return out


def target_lines(code, done, data, program, mode):
    if code not in data['courses']:
        return ['', f'{code}: not found in the course lists of the documents.']
    groups = course_entries(code, data, program)
    if not groups:
        return ['', f"{code} {data['courses'][code]['title'] or ''}: no prerequisite listed in the documents."]
    out = []
    for offering, entries in groups.items():
        g, line = judge(code, offering, entries, done, data, program)
        if mode == 'lookup':
            title = offering or data['courses'][code]['title'] or ''
            out += ['', f'{code} {title}. Prerequisites in the documents:'] + [
                f"- {p['text']} [{label(p['docs'], data, program)}]" for p in entries]
        else:
            out += ['', f'Result: ({g}) {dict(GROUPS)[g]}.', f'- {line}']
    return out


def unclear_card(done, cands, how, data):
    progs = data['programs']
    listed = '; '.join(f"{k} ({progs[k]['name']})" for k in cands) if cands else ''
    lines = [f"Completed courses given: {', '.join(done)}.",
             'The program could not be determined from the question'
             + (f'; these course codes are used by: {listed}.' if listed else '.'),
             'Ask the student which program they are in before listing courses.']
    card = make_card(lines, None, data, [], 'next')
    return {**card, 'title': 'Eligibility check: program unclear', 'section': 'which program?'}


def make_card(lines, program, data, targets, mode):
    docs = [data['docs'].get(d, {}) for d in (program or {}).get('docs', [])]
    url = next((d['url'] for d in docs if d.get('url')), '')
    of = f" ({program['key']} program)" if program else ''
    if mode == 'next':
        title = f"Eligibility check for {program['name']} ({program['key']})" if program else 'Eligibility check'
    elif mode == 'check':
        title = f"Eligibility check for {', '.join(targets)}{of}"
    else:
        title = f"Prerequisites of {', '.join(targets)}{of}"
    section = 'based on ' + ('; '.join(d.get('label', '') for d in docs) if docs else 'the course pages')
    text = '\n'.join(lines)
    return {'id': 0, 'chunk_id': DOC_ID, 'doc_id': DOC_ID, 'title': title, 'section': section,
            'status': 'current', 'rag_priority': 1, 'category': 'courses', 'doc_type': 'computed',
            'source_type': 'computed', 'url': url, 'source': '', 'approved_on': '', 'effective_date': '',
            'superseded_by': [], 'file': '', 'chunk_index': 0, 'words': len(text.split()), 'text': text,
            'context_text': '', 'origin': 'computed', 'published_at': None, 'score': 0.0,
            'added_reason': 'eligibility check'}
