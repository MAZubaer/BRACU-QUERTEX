"""Class-schedule lookup (no LLM): "CSE220 section 5" -> days, times and rooms of the
theory class and the lab with the same section number.

    lookup(question, history) -> None (not a schedule question) or
        {'pure': bool, 'text': answer in markdown, 'card': source card}

pure = the question only asks for a schedule, so app.py streams `text` as the answer
without an LLM call. Otherwise the card goes first in the sources and the LLM answers
(prompt rule 17).

The rows come from the class_schedule table (scripts/import_class_schedule.py, from the
official PDF). A term is used only while today (Asia/Dhaka) is between its valid_from and
valid_until (schedule_terms); after that the answer says the schedule ended and never
shows old times.

Rules
- Course: a code (CSE220, cse 220, CSE-220, CSE220L, Bangla digits) or a course name
  (titles from the course catalogue; several offered courses with that name -> ask).
- Section: "section 5", "sec 05", "s5", "CSE220-05", "(5)", "সেকশন ৫"; a question
  needs a section or a schedule word (schedule, time, room, class, lab, when, where...).
- Theory and lab match by section number, ignoring tags ("01 (BBA)"); every tagged
  variant of the asked number is shown with its tag.
- Duplicate meetings that differ only by a blank vs filled faculty are merged; if two
  printed versions of a section differ in day, time or room, both are shown.
"""
import json
import re
import time as _time
from collections import OrderedDict
from datetime import date
from pathlib import Path

from . import config, db

DOC_ID = 'class_schedule'
TTL = 600                                  # seconds a loaded term stays cached
_cache = {}

BN_DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
CODE = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{2,4})[ \t]?-?[ \t]?(\d{3})([A-Za-z]{0,2})(?![A-Za-z0-9])')
SECTION_PATTERNS = [
    re.compile(r'(?:\bsections?|\bsects?\b|\bsecs?\b|সেকশন\w*|সেক\b)\s*\.?\s*(?:no\.?|number|#|:)?\s*(\d{1,2})(?!\d)',
               re.I),
    re.compile(r'(?<![A-Za-z0-9])[sS](\d{1,2})(?!\d)'),
    re.compile(r'\(\s*(\d{1,2})\s*\)'),
]
AFTER_CODE = [re.compile(r'^\s*[-–/]\s*(\d{1,2})(?!\d)'),          # CSE220-05
              re.compile(r'^\s*\(\s*(\d{1,2})\s*\)'),               # CSE220 (5)
              re.compile(r'^\s+(\d{1,2})(?=\s*(?:$|[?.!,]))')]      # cse 220 05
SCHEDULE_WORDS = re.compile(
    r'\b(?:schedules?|times?|timings?|room|rooms|class|classes|labs?|when|where|routine|slots?|days?|'
    r'venue|sections?|secs?|faculty|teacher|instructor|meets?|held|offered|offering|running)\b|'
    r'কখন|কোথায়|কোথায়|রুম|ক্লাস|সময়|সময়|রুটিন|ল্যাব|সেকশন|টাইম', re.I)
LAB_WORD = re.compile(r'\b(?:labs?|laboratory)\b|ল্যাব', re.I)
THEORY_WORD = re.compile(r'\btheory\b|থিওরি', re.I)
NOT_CLASSES = re.compile(r'\b(?:exams?|final|finals|mid|midterms?|mid-terms?|quiz|quizzes|'
                         r'registration|advising|pre-?registration)\b|পরীক্ষা', re.I)
FILLER = set('''what whats what's is are the of for my in at and which does do give me show tell please pls plz
about a an to i course courses on it its will be there any this that semester fall term theory lab labs info
information details detail find need know want get can you with has have u its whens wheres where's when's
schedule schedules time times timing timings room rooms class classes when where routine slot slots day days
venue section sections sec secs no number faculty teacher instructor meet meets held hold take taking taken
our its your his her their st nd rd th also both all list see check now current currently
who teaches teaching taught teach takes initial initials offered offering running being'''.split())
FILLER_BN = set('এর কখন কোথায় কোথায় কি কী টা আমার হবে হয় কোন দিন এ তে কবে ও আর বলো বলুন বলেন জানতে চাই এবং '
                'সেকশন সেকশনের সেকশনে ক্লাস ক্লাসের ক্লাসটা ক্লাসগুলো রুম রুমে রুমটা টাইম সময় সময় ল্যাব '
                'ল্যাবের রুটিন একটু দাও দেন দিন জানাও কোর্স কোর্সের থিওরি'.split())
GENERIC_TITLES = {'thesis', 'project', 'seminar', 'internship', 'physics', 'ethics', 'independent study',
                  'special topics', 'research', 'dissertation', 'thesis / project', 'design', 'painting'}
DAYS = ['Saturday', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']


# ---------------------------------------------------------------- data
class Term:
    """The rows of one schedule term, grouped by base course ("CSE 220")."""

    def __init__(self, name, valid_from, valid_until, rows, source_file=''):
        self.name, self.valid_from, self.valid_until = name, valid_from, valid_until
        self.source_file = source_file
        self.by_base = {}
        for r in sorted(rows, key=lambda r: r['sl']):
            self.by_base.setdefault(r['base_course'], []).append(r)
        self.base_of = {r['course']: r['base_course'] for r in rows}
        self.courses = set(self.base_of)
        self.prefixes = {re.match(r'[A-Z]+', c).group() for c in self.courses}


def _load_term(today):
    """('active', Term) | ('ended', name, valid_until) | (None,) from Postgres (cached)."""
    key = today.isoformat()
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < TTL:
        return hit[1]
    rows = db.fetchall("""SELECT term, valid_from, valid_until, source_file FROM schedule_terms
                          ORDER BY valid_until DESC""")
    active = [r for r in rows if r[1] <= today <= r[2]]
    if active:
        name, vf, vu, src = active[0]
        cols = ['sl', 'course', 'course_norm', 'is_lab', 'base_course', 'section_number', 'section_tag',
                'day', 'day_order', 'start_time', 'end_time', 'room', 'faculty']
        data = db.fetchall(f"SELECT {', '.join(cols)} FROM class_schedule WHERE term = %s", (name,))
        state = ('active', Term(name, vf, vu, [dict(zip(cols, r)) for r in data], src))
    else:
        ended = [r for r in rows if r[2] < today]
        state = ('ended', ended[0][0], ended[0][2]) if ended else (None,)
    _cache.clear()
    _cache[key] = (_time.time(), state)
    return state


_titles = None


def course_titles():
    """{"CSE 220": ["Data Structures", "Data Structure"]} from the course catalogue
    (the course_catalog table; data/course_catalog.json adds more names when present)."""
    global _titles
    if _titles is not None:
        return _titles
    out = {}
    try:
        from . import eligibility
        for code, c in (eligibility.load().get('courses') or {}).items():
            if c.get('title'):
                out.setdefault(code, []).append(c['title'])
    except Exception as e:                       # no catalogue: names just aren't recognised
        print(f'[schedule] course titles: {type(e).__name__}: {e}')
    path = Path(__file__).resolve().parent.parent / 'data' / 'course_catalog.json'
    if path.exists():
        for code, c in json.loads(path.read_text(encoding='utf-8')).get('courses', {}).items():
            names = [c.get('title')] + list(c.get('also_listed_as') or []) + list(c.get('title_variants') or [])
            for n in names:
                n = n.get('title') if isinstance(n, dict) else n
                if n and n not in out.setdefault(code, []):
                    out[code].append(n)
    _titles = out
    return out


# ---------------------------------------------------------------- reading the question
def month_year(d):
    return f'{d:%B %Y}'


def find_code(text, term):
    """(base course, the code as asked, (start, end), how) for the first course code
    with a known prefix; how = 'found' | 'missing' | 'several' (CSE490 -> CSE490A, B...)."""
    for m in CODE.finditer(text):
        prefix, num, suffix = m.group(1).upper(), m.group(2), m.group(3).upper()
        if prefix not in term.prefixes:
            continue
        code = prefix + num + suffix
        if code in term.courses:
            return term.base_of[code], code, m.span(), 'found'
        if suffix == 'L' and prefix + num in term.courses:       # "CSE220L" asked, only theory listed
            return term.base_of[prefix + num], code, m.span(), 'found'
        variants = sorted({b for c, b in term.base_of.items() if c.startswith(prefix + num) and c != code})
        if variants and not suffix:
            return variants, code, m.span(), 'several'
        return f'{prefix} {num}{suffix}', code, m.span(), 'missing'
    return None


def find_name(text, term, titles):
    """(offered base courses, other codes with that name, the name, span) for the longest
    course name in the question, or None."""
    low = text.lower()
    best = None
    for code, names in titles.items():
        for n in names:
            nl = n.lower().strip()
            if nl in GENERIC_TITLES or (len(nl.split()) < 2 and len(nl) < 7):
                continue
            for m in re.finditer(r'(?<![a-z0-9])' + re.escape(nl) + r'(?![a-z0-9])', low):
                if best is None or (m.end() - m.start()) > (best[2][1] - best[2][0]):
                    best = (nl, n, m.span())
    if not best:
        return None
    nl = best[0]
    codes = sorted({c for c, names in titles.items() if any(x.lower().strip() == nl for x in names)})
    offered = [c for c in codes if c in term.by_base]
    return offered, codes, best[1], best[2]


def find_section(text, code_span=None):
    if code_span:
        after = text[code_span[1]:]
        for p in AFTER_CODE:
            m = p.match(after)
            if m:
                return m.group(1).zfill(2), (code_span[1] + m.start(1), code_span[1] + m.end(1))
    for p in SECTION_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(1).zfill(2), m.span()
    return None, None


def is_pure(text, spans):
    """Nothing asked besides the schedule: no word left after removing the course, the
    section, schedule words and filler words."""
    keep = list(text)
    for s, e in spans:
        for i in range(s, e):
            keep[i] = ' '
    rest = re.findall(r'[A-Za-zঀ-৿\']+', ''.join(keep).lower())
    left = [w for w in rest if w not in FILLER and w not in FILLER_BN and not re.fullmatch(r'[a-z]', w)]
    return not left


def previous_question(history):
    for m in reversed(history or []):
        if m.get('role') == 'user':
            return m.get('content') or ''
    return ''


# ---------------------------------------------------------------- formatting
def fmt_time(t):
    return f'{t.hour % 12 or 12}:{t.minute:02d}'


def fmt_range(a, b):
    ap = lambda t: 'AM' if t.hour < 12 else 'PM'          # noqa: E731
    if ap(a) == ap(b):
        return f'{fmt_time(a)}–{fmt_time(b)} {ap(b)}'
    return f'{fmt_time(a)} {ap(a)}–{fmt_time(b)} {ap(b)}'


def join_days(days):
    return days[0] if len(days) == 1 else ', '.join(days[:-1]) + ' & ' + days[-1]


def section_label(number, tag):
    return f'Section {number}' + (f' ({tag})' if tag else '')


def compress(numbers):
    """['01', '02', '03', '05'] -> '01–03, 05'."""
    nums = sorted({int(n) for n in numbers})
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(f'{nums[i]:02d}' if i == j else f'{nums[i]:02d}–{nums[j]:02d}')
        i = j + 1
    return ', '.join(out)


def versions(rows):
    """The printed versions of one section.
    Returns [{'meetings': [(day_order, day, start, end, room)], 'faculty': [...]}].

    Usually one: all the section's meetings, with identical meetings merged (a filled
    faculty wins over a blank one). A section can have more meetings than its days
    suggest: ARC studios meet twice on Sunday, and some theory codes carry their lab
    as an extra row (CSE250: Sat & Thu in 09A-07C plus Sat 11:00 in 12B-20L).
    Several versions only when the section is printed more than once: blocks of rows
    (split where the SL numbers jump, or a block that is the same days twice, e.g.
    Mon, Wed, Mon, Wed) with the same days and the same kinds of rooms. Identical
    versions are merged (ANT101 sec 10 "10 (BDM)" and "10- BDM"); different ones are
    all returned."""
    rows = sorted(rows, key=lambda r: r['sl'])
    blocks = [[rows[0]]]
    for prev, r in zip(rows, rows[1:]):
        if r['sl'] == prev['sl'] + 1:
            blocks[-1].append(r)
        else:
            blocks.append([r])
    split = []
    for b in blocks:
        h = len(b) // 2
        if h and len(b) % 2 == 0 and [r['day'] for r in b[:h]] == [r['day'] for r in b[h:]]:
            split += [b[:h], b[h:]]
        else:
            split.append(b)

    def meetings(b):
        return sorted({(r['day_order'], r['day'], r['start_time'], r['end_time'], r['room']) for r in b})

    def faculty(b):
        return [f for f in OrderedDict.fromkeys(r['faculty'] for r in b) if f]

    def shape(b):                       # the days and room kinds (C, L, T...) of a block
        return sorted((r['day'], r['room'][-1:]) for r in b)

    # single rows can't show a reprint (CSE472 meets twice on Monday: 2:00 and 3:30)
    if len(split) > 1 and len(split[0]) > 1 and all(shape(b) == shape(split[0]) for b in split):
        out = []
        for b in split:
            m, f = meetings(b), faculty(b)
            same = next((v for v in out if v['meetings'] == m), None)
            if same:
                same['faculty'] += [x for x in f if x not in same['faculty']]
            else:
                out.append({'meetings': m, 'faculty': f})
        return out
    return [{'meetings': meetings(rows), 'faculty': faculty(rows)}]


def meeting_text(v, rooms=True, faculty=True):
    """"Sunday & Tuesday, 2:00–3:20 PM, Room 09H-34C (Faculty: AJA)"."""
    groups = OrderedDict()
    for order, day, start, end, room in v['meetings']:
        groups.setdefault((start, end, room if rooms else ''), []).append((order, day))
    parts = []
    for (start, end, room), days in sorted(groups.items(), key=lambda g: (g[1][0][0], g[0][0])):
        parts.append(f"{join_days([d for _, d in days])}, {fmt_range(start, end)}"
                     + (f', Room {room}' if room else ''))
    text = '; '.join(parts)
    if faculty and v['faculty']:
        text += f" (Faculty: {', '.join(v['faculty'])})"
    return text


def section_groups(rows):
    """{(number, tag): rows} in section order."""
    out = OrderedDict()
    for r in sorted(rows, key=lambda r: (r['section_number'], r['section_tag'] or '', r['sl'])):
        out.setdefault((r['section_number'], r['section_tag']), []).append(r)
    return out


def describe_rows(rows, rooms=True, faculty=True):
    vs = versions(rows)
    if len(vs) == 1:
        return meeting_text(vs[0], rooms, faculty)
    return (f'the schedule lists {len(vs)} versions: '
            + ' | '.join(f'version {i}: {meeting_text(v, rooms, faculty)}' for i, v in enumerate(vs, 1)))


# ---------------------------------------------------------------- answers
def course_label(base, titles):
    t = (titles.get(base) or [None])[0]
    return f'{base} ({t})' if t else base


def lab_label(base, lab_codes, titles):
    """"CSE423L (lab of CSE 423 (Computer Graphics))"."""
    return f"{', '.join(lab_codes)} (lab of {course_label(base, titles)})"


def no_lab_line(base, term, titles):
    return f'{course_label(base, titles)} has no separate lab in the {term.name} schedule.'


def section_answer(term, base, number, titles, lab_only=False):
    rows = term.by_base[base]
    theory = section_groups([r for r in rows if not r['is_lab']])
    labs = section_groups([r for r in rows if r['is_lab']])
    lab_codes = sorted({r['course'] for r in rows if r['is_lab']})
    t_hits = [(k, v) for k, v in theory.items() if k[0] == number]
    l_hits = [(k, v) for k, v in labs.items() if k[0] == number]
    if lab_only and labs:
        # "CSE423 lab section 2" / "CSE423L section 2": the lab only
        head = f'**{lab_label(base, lab_codes, titles)}, Section {number} — {term.name}**'
        if not l_hits:
            return head, [f"{', '.join(lab_codes)} has no section {number} in the {term.name} schedule "
                          f'(lab sections: {compress(k[0] for k in labs)}).']
        return head, [f'- Lab ({v[0]["course"]}, {section_label(num, tag)}): {describe_rows(v)}'
                      for (num, tag), v in l_hits]
    head = f'**{course_label(base, titles)}, Section {number} — {term.name}**'
    lines = [no_lab_line(base, term, titles)] if lab_only else []
    if not t_hits and not l_hits:
        lines.append(f'Section {number} of {base} is not in the {term.name} class schedule.')
        if theory:
            lines.append(f'Sections: {compress(k[0] for k in theory)}.')
        if labs:
            lines.append(f"Lab sections ({', '.join(lab_codes)}): {compress(k[0] for k in labs)}.")
        return head, lines
    if t_hits:
        for (num, tag), v in t_hits:
            label = 'Theory' + (f', {section_label(num, tag)}' if len(t_hits) > 1 or tag else '')
            lines.append(f'- {label}: {describe_rows(v)}')
    elif theory:
        lines.append(f'- Theory: {base} has no theory section {number} in the schedule '
                     f'(theory sections: {compress(k[0] for k in theory)}).')
    if l_hits:
        for (num, tag), v in l_hits:
            code = v[0]['course']
            lines.append(f'- Lab ({code}, {section_label(num, tag)}): {describe_rows(v)}')
    elif labs:
        lines.append(f"- Lab: {', '.join(lab_codes)} has no section {number} "
                     f'(lab sections: {compress(k[0] for k in labs)}).')
    return head, lines


def course_answer(term, base, titles, lab_only=False):
    rows = term.by_base[base]
    theory = section_groups([r for r in rows if not r['is_lab']])
    labs = section_groups([r for r in rows if r['is_lab']])
    lab_codes = sorted({r['course'] for r in rows if r['is_lab']})
    if lab_only and labs:
        # "cse423 lab time" / "CSE423L schedule": the lab sections, not the theory ones
        n = len({k[0] for k in labs})
        head = (f"**{lab_label(base, lab_codes, titles)} — {term.name}: "
                f"{n} lab section{'s' if n != 1 else ''}**")
        lines = [f'- {section_label(num, tag)}: {describe_rows(v, rooms=False, faculty=False)}'
                 for (num, tag), v in labs.items()]
        if n > 10:
            lines.append('\nWhich section are you in? Ask again with the section number (for example '
                         f'"{lab_codes[0]} section {next(iter(labs))[0]}") for the room.')
        return head, lines
    n = len({k[0] for k in theory}) or len({k[0] for k in labs})
    head = f"**{course_label(base, titles)} — {term.name}: {n} section{'s' if n != 1 else ''}**"
    lines = [no_lab_line(base, term, titles), ''] if lab_only else []
    lines += [f'- {section_label(num, tag)}: {describe_rows(v, rooms=False, faculty=False)}'
              for (num, tag), v in (theory or labs).items()]
    if theory and labs:
        lines.append(f"\nLab sections ({', '.join(lab_codes)}): {compress(k[0] for k in labs)}. "
                     'Ask with your section number for the lab time and room.')
    if n > 10:
        lines.append('\nWhich section are you in? Ask again with the section number (for example '
                     f'"{base.replace(" ", "")} section {next(iter(theory or labs))[0]}") for the rooms and the lab.')
    return head, lines


def make_card(term_name, valid_until, text):
    return {'id': 0, 'chunk_id': DOC_ID, 'doc_id': DOC_ID,
            'title': f'Class Schedule, {term_name} (official PDF)',
            'section': f'Valid until {month_year(valid_until)}' if valid_until else '',
            'status': 'current', 'rag_priority': 1, 'category': 'schedule', 'doc_type': 'computed',
            'source_type': 'computed', 'url': '', 'source': '', 'approved_on': '', 'effective_date': '',
            'superseded_by': [], 'file': '', 'chunk_index': 0, 'words': len(text.split()), 'text': text,
            'context_text': '', 'origin': 'computed', 'published_at': None, 'score': 0.0,
            'added_reason': 'class schedule'}


def lookup(question, history=None, today=None, state=None, titles=None):
    """The schedule answer for this question, or None (see the module docstring)."""
    if not config.CLASS_SCHEDULE:
        return None
    text = (question or '').translate(BN_DIGITS)
    if NOT_CLASSES.search(text):
        return None
    try:
        state = state or _load_term(today or config.today())
    except Exception as e:                      # no tables yet, database down: no schedule answers
        print(f'[schedule] {type(e).__name__}: {e}')
        return None
    if state[0] is None:
        return None
    titles = titles if titles is not None else course_titles()
    if state[0] == 'ended':
        _, name, until = state
        probe = Term(name, None, until, [])
        known = {c.split()[0] for c in titles}             # course prefixes, not "Room 101"
        code = next((m for m in CODE.finditer(text) if m.group(1).upper() in known), None)
        hit = None if code else find_name(text, probe, titles)
        number, sspan = find_section(text, code.span() if code else None)
        if not (code or hit) or not (number or SCHEDULE_WORDS.search(text)):
            return None
        spans = [code.span() if code else hit[3]] + ([sspan] if sspan else [])
        msg = (f"The {name} class schedule ended in {month_year(until)}. The new semester's schedule "
               "hasn't been added yet.")
        return {'pure': is_pure(text, spans), 'text': msg, 'card': make_card(name, until, msg)}
    term = state[1]
    found = find_code(text, term)
    name_hit = None if found else find_name(text, term, titles)
    spans = []
    if not found and not name_hit:
        # a follow-up like "and section 7?" borrows the course from the previous question
        number, sspan = find_section(text)
        if not number or len(text.split()) > 6:
            return None
        prev = previous_question(history).translate(BN_DIGITS)
        found = find_code(prev, term) if prev else None
        name_hit = None if found else (find_name(prev, term, titles) if prev else None)
        if not found and not name_hit:
            return None
        code_span = None
    else:
        code_span = found[2] if found else name_hit[3]
        number, sspan = find_section(text, code_span)
        spans.append(code_span)
        if not number and not SCHEDULE_WORDS.search(text):
            return None
    if sspan:
        spans.append(sspan)
    pure = is_pure(text, spans)

    if name_hit:
        offered, codes, name, _ = name_hit
        if not offered:
            body = (f"{name} ({', '.join(codes)}) is not offered in {term.name} according to the "
                    f"official class schedule.")
            return finish(term, None, [body], pure)
        if len(offered) > 1 and LAB_WORD.search(text) and not THEORY_WORD.search(text):
            # "Computer Graphics lab": of ARC 214 and CSE 423 only CSE 423 has a lab
            with_lab = [b for b in offered if any(r['is_lab'] for r in term.by_base[b])]
            offered = with_lab if len(with_lab) == 1 else offered
        if len(offered) > 1:
            listed = ', '.join(offered)
            body = f'"{name}" matches several courses in the {term.name} schedule: {listed}. Which one do you mean?'
            return finish(term, None, [body], pure)
        base = offered[0]
    else:
        base, asked, _, how = found
        if how == 'missing':
            label = course_label(base, titles)
            body = f'{label} is not offered in {term.name} according to the official class schedule.'
            return finish(term, None, [body], pure)
        if how == 'several':
            listed = ', '.join(course_label(b, titles) for b in base)
            body = f'{asked.upper()} is listed in {term.name} as: {listed}. Which one do you mean?'
            return finish(term, None, [body], pure)
    # the lab only: a lab code was asked (CSE423L), or "lab" without "theory" ("cse423 lab time")
    asked_lab = bool(found) and found[3] == 'found' and found[1][-1:] == 'L' and \
        found[1] != base.replace(' ', '')
    lab_only = asked_lab or bool(LAB_WORD.search(text) and not THEORY_WORD.search(text))
    if number:
        head, lines = section_answer(term, base, number, titles, lab_only)
    else:
        head, lines = course_answer(term, base, titles, lab_only)
    return finish(term, head, lines, pure)


def finish(term, head, lines, pure):
    body = '\n'.join(([head, ''] if head else []) + lines)
    text = (body + f'\n\nFrom the official {term.name} class schedule [S1]. '
            f'Valid until {month_year(term.valid_until)}.')
    return {'pure': pure, 'text': text, 'card': make_card(term.name, term.valid_until, body)}
