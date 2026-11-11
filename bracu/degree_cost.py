"""Total cost of a bachelor's degree, computed from the documents (no LLM).

    "What is the total expense to graduate in CSE in 4 years?"     -> CSE card
    "How much does the LLB cost?"                                   -> LLB card
    "Total cost of CSE vs EEE?"                                     -> both programs

card_for(question) returns a source card (a chunk-like dict, doc_type 'computed',
doc_id 'degree_cost') that the retriever puts first, or None when the question isn't
about the total cost of an undergraduate program.

The estimate = admission application fee + admission fee + library membership fee
(one time each) + credits x tuition per credit + semesters x semester fee + the
Residential Semester (RS) fee once (compulsory for every undergraduate, on top of the
regular semester fees). Semesters = years x semesters a year: BRAC University runs
3 a year (Spring, Summer, Fall); Pharmacy and LLB run 2 (8 semesters in four years).
Architecture is 5 years (15 semesters) as its page recommends. A number of years or
semesters in the question replaces the program's length.

The facts (each with the quote it was read from) come from the course_catalog table,
key 'degree_cost', written by scripts/build_degree_costs.py --load-db.
"""
import re

from . import db

DOC_ID = 'degree_cost'
_data = None

COST = re.compile(r"\b(?:costs?|costing|expenses?|expenditure|spend(?:ing)?|budget)\b|খরচ", re.I)
FEE = re.compile(r"\b(?:fees?|tuition|pay|paying|payment|money|amount|how\s+much)\b|ফি|টাকা", re.I)
TOTAL = re.compile(r"\b(?:total|whole|entire|full|overall|altogether|all\s+together|graduat\w*|"
                   r"complet\w*|finish\w*|years?|semesters\s+in\s+all)\b|মোট|পুরো", re.I)
SPECIFIC = re.compile(r"\bper\s+(?:credit|semester|course)\b|\b(?:semester|admission|application|library|"
                      r"residential\s+semester|rs|late|retake|lab|transcript|exam)\s+fees?\b|"
                      r"\b(?:waiver|scholarship|installments?|refund\w*)\b", re.I)
POSTGRAD = re.compile(r"\b(?:masters?|mba|emba|post-?\s?graduate|ph\.?\s?d|m\.?\s?sc|m\.?\s?engg|llm|"
                      r"m\.?\s?a\b|mph|mds|pgd)\b", re.I)
YEARS = re.compile(r"\b(\d{1,2}|three|four|five|six)\s*(?:-\s*)?(?:years?|yrs?)\b", re.I)
SEMESTERS = re.compile(r"\b(\d{1,2})\s*semesters?\b", re.I)
NUM = {'three': 3, 'four': 4, 'five': 5, 'six': 6}


def load():
    """The degree-cost facts from Postgres (once per server instance)."""
    global _data
    if _data is None:
        row = db.fetchone("SELECT value FROM course_catalog WHERE key = 'degree_cost'")
        _data = row[0] if row else {}
    return _data


def tk(n):
    return f'Tk {n:,.0f}'


def asks_total_cost(question):
    if POSTGRAD.search(question):
        return False
    if COST.search(question):
        return bool(TOTAL.search(question)) or not SPECIFIC.search(question)
    return bool(FEE.search(question) and TOTAL.search(question)
                and (not SPECIFIC.search(question) or re.search(r'\btotal\b', question, re.I)))


def find_programs(question, programs):
    """Program keys named in the question, in order of appearance (nested names: the longest)."""
    low = question.lower().replace('&', 'and')
    spans = []
    for key, p in programs.items():
        for m in re.finditer(r'(?<![A-Za-z])' + re.escape(key) + r'(?![A-Za-z])', question):
            spans.append((m.start(), m.end(), key))
        for name in p['names']:
            n = name.lower().replace('&', 'and')
            for m in re.finditer(r'(?<![a-z])' + re.escape(n) + r'(?![a-z])', low):
                spans.append((m.start(), m.end(), key))
    keep = [(s, e, k) for s, e, k in spans
            if not any(s2 <= s and e <= e2 and (e2 - s2) > (e - s) for s2, e2, _ in spans)]
    out = []
    for _, _, k in sorted(keep):
        if k not in out:
            out.append(k)
    return out


def asked_length(question):
    """(years, semesters) given in the question, else (None, None)."""
    m = SEMESTERS.search(question)
    if m:
        return None, int(m.group(1))
    m = YEARS.search(question)
    if m:
        y = m.group(1).lower()
        return NUM.get(y) or int(y), None
    return None, None


def estimate(p, fees, years=None, semesters=None):
    """The cost lines of one program: {'rows': [(item, how, low, high)], 'low', 'high', ...}."""
    g = p['fee_group']
    per_year = p['per_year']
    length = p['length']
    if semesters:
        n, how_n = semesters, f'{semesters} semesters (as given in the question)'
    elif years:
        n = years * per_year
        how_n = f'{years} years (as given in the question) × {per_year} semesters a year'
    else:
        n = length['semesters']
        how_n = (f"{length['years']} years × {per_year} semesters a year"
                 + (' (length stated on the program page)' if length['how'] == 'stated'
                    else ' (standard four-year bachelor\'s)'))
    credits = p['credits']['value']
    rate = fees['per_credit'][g]['value']
    rows = [('Admission application fee', 'one time', fees['application']['value']),
            ('Admission fee', 'one time', fees['admission']['value']),
            ('Library membership fee', 'one time', fees['library']['value'])]
    if g == 'ARC':
        studio = fees['per_credit']['ARC_STUDIO']['value']
        rows.append(('Tuition', f'{credits} credits × {tk(rate)} (lecture) to {tk(studio)} (studio) '
                                f'per credit; the page does not give the studio/lecture split',
                     (credits * rate, credits * studio)))
    else:
        rows.append(('Tuition', f'{credits} credits × {tk(rate)} per credit', credits * rate))
    sem = fees['semester_fee'][g]['value']
    rows.append(('Semester fee', f'{n} semesters × {tk(sem)}', n * sem))
    rows.append(('Residential Semester (RS) fee', 'once, compulsory for all undergraduates (taken in '
                 'the 3rd or 4th semester), on top of the semester fees', fees['rs']['value']))
    low = sum(v[0] if isinstance(v, tuple) else v for _, _, v in rows)
    high = sum(v[1] if isinstance(v, tuple) else v for _, _, v in rows)
    return {'rows': rows, 'low': low, 'high': high, 'semesters': n, 'how_semesters': how_n,
            'recommended': length['semesters']}


def amount(v):
    return f'{tk(v[0])} to {tk(v[1])}' if isinstance(v, tuple) else tk(v)


def program_lines(p, fees, years, semesters):
    e = estimate(p, fees, years, semesters)
    total = tk(e['low']) if e['low'] == e['high'] else f"{tk(e['low'])} to {tk(e['high'])}"
    lines = [f"### {p['name']} ({p['key']})",
             f"Semesters counted: {e['semesters']} = {e['how_semesters']}."]
    if e['semesters'] != e['recommended']:
        lines.append(f"(The program's own length is {p['length']['years']} years = "
                     f"{e['recommended']} semesters.)")
    lines += ['| Item | Calculation | Amount |', '|---|---|---|']
    lines += [f'| {item} | {how} | {amount(v)} |' for item, how, v in e['rows']]
    lines.append(f'| **Estimated total** | | **{total}** |')
    c = p['credits']
    lines.append(f"Credits: {c['value']} — "
                 + (f"\"{c['quote']}\"" if c['how'] == 'stated' else c['quote'])
                 + f" ({p['doc']['title']}).")
    if p['length']['how'] == 'stated':
        lines.append(f"Length: \"{p['length']['quote']}\" ({p['doc']['title']}).")
    return lines


def card_for(question, data=None):
    """The computed degree-cost card for this question, or None."""
    if not asks_total_cost(question):
        return None
    try:
        data = data if data is not None else load()
    except Exception as e:                  # no table yet, database down: no card
        print(f'[degree_cost] {type(e).__name__}: {e}')
        return None
    if not data or not data.get('programs'):
        return None
    programs, fees = data['programs'], data['fees']
    keys = find_programs(question, programs)[:3]
    years, semesters = asked_length(question)
    head = (f"Estimated total cost, computed by the app (not by the AI) from the {fees['title']} page "
            f"(fees for {fees['applies_to']}, local students) and each program's own page.")
    if not keys:
        listed = ', '.join(f"{k} ({p['name']})" for k, p in programs.items())
        lines = [head, 'The program could not be determined from the question. Ask the student which '
                 f'undergraduate program they mean. Programs with a computed estimate: {listed}.',
                 f"Formula: admission application fee {tk(fees['application']['value'])} + admission fee "
                 f"{tk(fees['admission']['value'])} + library membership fee {tk(fees['library']['value'])} "
                 f"+ credits × tuition per credit + semesters × semester fee + Residential Semester fee "
                 f"{tk(fees['rs']['value'])} once."]
        return make_card(lines, 'Degree cost estimate: program unclear', fees, [])
    lines = [head]
    for k in keys:
        lines += program_lines(programs[k], fees, years, semesters)
    cal = data.get('calendar') or {}
    if cal:
        lines.append(f"Semesters a year: \"{cal['quote']}\" ({cal['doc']['title']}); Pharmacy and LLB "
                     f"have 2 a year (8 semesters in four years).")
    lines += [
        'Each extra semester adds one more semester fee; retaken or extra courses add tuition per credit.',
        'Not included: non-credit or extra freshman courses the admission test may prescribe '
        f"(the first course is in the admission fees; each extra one {tk(fees['remedial_course']['value'])}, "
        f"Law {tk(fees['remedial_course_law']['value'])}), other fees (Other Fees page), and any scholarship "
        'or waiver, which lowers the cost.',
        f"\"{fees['may_change']}\"",
    ]
    names = ', '.join(programs[k]['key'] for k in keys)
    return make_card(lines, f'Degree cost estimate for {names}', fees, [programs[k] for k in keys])


def make_card(lines, title, fees, progs):
    text = '\n'.join(lines)
    section = 'based on ' + '; '.join([fees['title']] + [p['doc']['title'] for p in progs])
    return {'id': 0, 'chunk_id': DOC_ID, 'doc_id': DOC_ID, 'title': title, 'section': section,
            'status': 'current', 'rag_priority': 1, 'category': 'fees', 'doc_type': 'computed',
            'source_type': 'computed', 'url': fees.get('url', ''), 'source': '', 'approved_on': '',
            'effective_date': '', 'superseded_by': [], 'file': '', 'chunk_index': 0,
            'words': len(text.split()), 'text': text, 'context_text': '', 'origin': 'computed',
            'published_at': None, 'score': 0.0, 'added_reason': 'degree cost'}
