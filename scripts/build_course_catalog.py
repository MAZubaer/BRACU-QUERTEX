"""Build the course catalogue: one card per course code, compiled from the corpus.

    python scripts/build_course_catalog.py            # write the catalogue
    python scripts/build_course_catalog.py --check    # parse and report only

Reads every document in BRACU_RAG_Corpus/ (not _duplicates*, not the catalogue
itself), finds course definitions in all the formats the program pages use
(table rows, "CODE- Title" / "CODE: Title" cells, <br> lists in cells, bold /
plain / bullet / numbered lines, headings with a description and a
prerequisite line, the EEE/ECE course-outline tables, table prerequisite
columns) and option lists ("Minimum one from: PSY101, SOC101 ..."), and
merges them per code.

Writes
- BRACU_RAG_Corpus/00_START_HERE/course_catalog/<PREFIX>.md: one "## CODE — Title"
  section per course (the chunker gives each its own chunk), GENERATED: never
  edit by hand; rerun this script, then scripts/ingest.py.
- data/course_catalog.json: code -> card.

When documents disagree nothing is picked silently: every differing title and
credit value is kept with its source. Typos in the sources are not corrected.
"""
import argparse
import difflib
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from chunker import read_doc                          # noqa: E402

from bracu import config                              # noqa: E402

CORPUS = config.CORPUS_DIR
OUT_DIR = CORPUS / '00_START_HERE' / 'course_catalog'
OUT_JSON = ROOT / 'data' / 'course_catalog.json'

# tokens that look like course codes but are not (taka amounts, ISBN, the address
# "KHA 224", a cited report "HTA 101", the typo "BU201" in the RS handbook)
NOT_COURSES = {'BDT', 'ISBN', 'KHA', 'IEEE', 'JPEG', 'AI', 'HTA', 'BU'}
NO_TITLES_FROM = {'web_submission_guidelines_for_design_portfolio'}   # project topics, not titles

CODE = r'(?P<p>[A-Z]{2,4}) ?(?P<n>\d{3})(?P<s>[A-Z])?(?![A-Za-z0-9])'
CODE_RE = re.compile(r'(?<![A-Za-z0-9])' + CODE)
# a group of codes sharing parts: "EEE/ECE 283", "EEE101/101L", "EMB 101/ DEV 101"
GROUP_RE = re.compile(r'(?<![A-Za-z0-9])(?P<ps>(?:[A-Z]{2,4}\s?/\s?)*[A-Z]{2,4}) ?(?P<n>\d{3}[A-Z]?)'
                      r'(?P<more>(?:\s?/\s?\d{3}[A-Z]?(?![A-Za-z0-9]))*)(?![A-Za-z0-9])')
LEAD = re.compile(r'^\s*(?:#{1,6}\s*)?(?:[●•▪·◦\-–*]\s+)?(?:\d{1,3}[.)]\s+)?')
CREDIT = re.compile(r'[(\[]?\s*[/–—-]?\s*\b(\d{1,2}(?:\.\d{1,2})?)\s*-?\s*(?:credit|cr\.?\b)s?[)\]]?', re.I)
NUM_CELL = re.compile(r'^\(?\s*(\d{1,2}(?:\.\d{1,2})?)\s*(?:credits?)?\s*\)?$', re.I)
PREREQ = re.compile(r'^\W*(?:pre-?\s?req(?:uisite)?s?\.?|prerequisites?)\s*\**\s*[:.]?\s*\**\s*(?P<t>.*)$', re.I)
BOOKS = re.compile(r'^\W*(suggested\s+(?:text\s*)?books?|suggested\s+texts?|recommended\s+(?:text\s*)?books?|'
                   r'text\s*books?|reference\s*books?|text\s+and\s+reference\s+books?|suggested\s+references?|'
                   r'recommended\s+readings?|'
                   r'references?|suggested\s+readings?|readings?|books?)\b', re.I)
STOP = re.compile(r'^\W*(course\s+outcomes?|learning\s+outcomes?|assessment|course\s+objectives?|'
                  r'[c-z]\.\s+course\s+objective|grading|evaluation|marks\s+distribution)\b', re.I)
DESC_HEAD = re.compile(r'^\W*(course\s+overview|course\s+description|course\s+content|description|'
                       r'objectives?:|topics?:)', re.I)
OUTLINE_KEY = re.compile(r'^(course\s+code|course\s+title|credit\s+hours?|contact\s+hours?|category|type|'
                         r'prerequisites?|co-?requisites?|equivalent\s+courses?)\b', re.I)
OPTION_WORDS = re.compile(r'one\s+from|any\s+(?:one|two|three)|choose|optional|options?|minimum|elective|'
                          r'select|from\s+the\s+following|either', re.I)
PROSE_START = re.compile(r'^(and|or|is|are|was|were|will|can|may|must|has|have|should|in|of|to|for|the|a)\b', re.I)
PROGRAM_TITLE = re.compile(r'bachelor|master|minor|degree plan|double major|mba|pgd|courses offered|'
                           r'course listing|curriculum|streams|detail of', re.I)


# ---------------------------------------------------------------- small helpers
def md(s):
    return re.sub(r'[*_`]+', '', s or '').replace('\\', '').strip()


def norm_code(p, n, s=''):
    return f'{p} {n}{s or ""}'


def codes_in(text):
    """All course codes in text, expanding shared parts: "EEE/ECE 283" -> EEE 283, ECE 283;
    "EEE101/101L" -> EEE 101, EEE 101L. Excludes non-course prefixes."""
    out = []
    for m in GROUP_RE.finditer(text):
        prefixes = [p.strip() for p in m.group('ps').split('/')]
        nums = [m.group('n')] + [x.strip() for x in m.group('more').split('/') if x.strip()]
        for p in prefixes:
            if p in NOT_COURSES:
                continue
            for n in nums:
                code = norm_code(p, n[:3], n[3:])
                if code not in out:
                    out.append(code)
    return out


def leading_codes(text):
    """(codes, rest) when text starts with one or more codes ("ANT 202 / SOC 201: Title")."""
    s = md(text).lstrip('#').strip()
    s = re.sub(r'^\d{1,3}[.)]\s+', '', s)
    m = re.match(r'^((?:(?:[A-Z]{2,4}\s?/\s?)*[A-Z]{2,4} ?\d{3}[A-Z]?(?:\s?/\s?\d{3}[A-Z]?)*\*?\s*(?:/|,|&|and)?\s*)+)'
                 r'(?P<rest>.*)$', s)
    if not m:
        return [], s
    head = m.group(1)
    codes = codes_in(head)
    if not codes:
        return [], s
    return codes, m.group('rest')


SLOT = re.compile(r'\b(?:program|university|school|ged|free|open|general)\s+(?:elective|core)\b|'
                  r'\belective\s+(?:[ivx]+|\d+)\b|\bcourse\s+\d\b', re.I)
CAT_NOISE = re.compile(r'teaching|objective|strateg|outcome|mapping|assessment|delivery|total\s+credit|'
                       r'requirement|program\s+flow|sequence|offered|structure|contact\s+hours|'
                       r'are\s+to\b|will\s+be|students?\s', re.I)
NOT_A_CATEGORY = re.compile(r'^(?:course\s+details?|course\s+descriptions?|course\s+outline|curriculum|'
                            r'courses?|course\s+list|course\s+code|description)$', re.I)


def clean_cat(t):
    """A category label: "## Program Core — 48 credits" -> "Program Core"."""
    t = md(t).lstrip('#').strip()
    t = re.sub(r'^course\s+level:\s*', '', t, flags=re.I)
    t = re.sub(r'\(?\s*\d+\s*courses?\s*[-–—]\s*\d+(?:\.\d)?\s*credits?\s*\)?', ' ', t, flags=re.I)
    t = re.sub(r'[-–—(]?\s*\b\d{1,3}(?:\.\d)?\s*credits?\b\)?', ' ', t, flags=re.I)
    t = re.sub(r'\s+', ' ', t).strip(' :—–-')
    if (not t or NOT_A_CATEGORY.match(t) or BOOKS.match(t) or STOP.match(t) or DESC_HEAD.match(t)
            or re.match(r'^[A-H]\.\s', t)                       # outline sub-heads "E. Mapping of CO-PO ..."
            or re.match(r'^CO\d', t) or re.search(r'\b[A-Z]{2,4}[0O]?\s?\d{3}\b', t)   # a course ("BI0 101 ...")
            or CAT_NOISE.search(t) or len(t.split()) > 8):
        return ''
    t = re.sub(r'^(?:\(?[a-h]\)|[a-h]\.|[ivx]+[.)]|\d{1,2}[.)])\s+', '', t, flags=re.I)  # "c. Elective Courses"
    if t.isupper():
        t = t.title()                                            # "PROGRAM CORE" -> "Program Core"
    return t[:80]


def split_pair(title):
    """"Digital Signal Processing Digital Signal Processing Laboratory" (a theory + lab
    row) -> ("Digital Signal Processing", "Digital Signal Processing Laboratory")."""
    w = title.split()
    for k in range(1, len(w) // 2 + 1):
        if w[k:2 * k] == w[:k] and len(w) > 2 * k - 1:
            return ' '.join(w[:k]), ' '.join(w[k:])
    return None


def credits_all(text):
    return [fmt_credit(m.group(1)) for m in CREDIT.finditer(text or '')]


def credits_of(text):
    m = CREDIT.search(text or '')
    return fmt_credit(m.group(1)) if m else None


def fmt_credit(x):
    v = float(x)
    return str(int(v)) if v == int(v) else str(v)


def clean_title(t):
    t = md(t)
    t = re.sub(r'\[[^\]]*semester[^\]]*\]', '', t, flags=re.I)
    t = CREDIT.sub(' ', t)
    t = re.sub(r'\s*[–—-]\s*v\d(?:\s*,\s*v\d)*\s*$', '', t)          # "– v1, v2" outline versions
    t = re.sub(r'[√✓*]+', ' ', t)
    t = re.sub(r'\(\s*\)', ' ', t)
    t = re.sub(r'^\s*[-–—:.,]\s*', '', t)
    t = re.sub(r'[\s(–—\-/:,;.]+$', '', t)
    t = re.sub(r'(?<=[A-Za-z)])\s+\d{1,2}(?:\.\d)?$', '', t)      # "Bangladesh Studies 3" (bare credits)
    return re.sub(r'\s+', ' ', t).strip()


def plausible_title(t):
    """Titles only: not a sentence, not a fragment, no other course code inside."""
    if not t or len(t) < 3 or not re.search(r'[A-Za-z]{3}', t):
        return False
    if CODE_RE.search(t) or PROSE_START.match(t) or t.startswith('('):
        return False
    return len(t.split()) <= 14 and len(t) <= 110


def norm_title(t):
    t = t.lower().replace('&', ' and ')
    t = re.sub(r'\(.*?\)', ' ', t)
    t = re.sub(r'\b(l|1)\b', 'i', t)
    t = re.sub(r'\bll\b', 'ii', t)
    t = re.sub(r'\blaboratory\b', 'lab', t)
    return ' '.join(re.findall(r'[a-z0-9]+', t))


def prereq_info(text):
    t = re.sub(r'\s+', ' ', md(text)).strip(' .:;')
    if t.count(')') > t.count('('):
        t = t.rstrip(')').strip()
    if t.count('(') > t.count(')'):
        t = t.lstrip('(').strip()
    low = t.lower()
    codes = codes_in(t)
    none = bool(re.fullmatch(r'(none|nil|n/?a|-+|no prerequisites?)', low)) or (not codes and low in ('', 'none'))
    perm = 'permission' in low or 'consent' in low
    if none:
        return {'text': 'None', 'codes': [], 'none': True, 'permission': False}
    only_codes = re.fullmatch(r'[A-Z0-9 ,/&;.and]+', t) is not None and codes
    shown = ', '.join(codes) if only_codes else t
    if perm and not codes:
        shown = 'Permission of Instructor'
    return {'text': shown[:220], 'codes': codes, 'none': False, 'permission': perm}


def level_of(code):
    n = int(code.split()[1][:3])
    if n < 100:
        return '000-level (foundation / remedial)'
    return f'{n // 100}00-level ({"graduate" if n >= 500 else "undergraduate"})'


# ---------------------------------------------------------------- parsing one document
class DocParser:
    """Collects definitions and option-list mentions from one document."""

    def __init__(self, doc_id, title, doc_type):
        self.doc, self.title, self.doc_type = doc_id, title, doc_type
        self.defs, self.mentions = [], []
        self.heading_cat = ''
        self.block = None                # open course block: description / prerequisite / books
        self.outline = None              # EEE/ECE course-outline key table

    # ---- records
    def define(self, codes, title, kind, credits=None, prereq=None, category=None):
        for i, code in enumerate(codes):
            self.defs.append({
                'code': code, 'title': title, 'kind': kind, 'doc': self.doc, 'credits': credits,
                'prereq': prereq, 'category': category or self.heading_cat,
                'equivalents': [c for c in codes if c != code], 'desc': [], 'books': []})
        return self.defs[-len(codes):] if codes else []

    def mention(self, codes, category):
        for c in codes:
            self.mentions.append({'code': c, 'doc': self.doc, 'category': category})

    # ---- description blocks
    def open_block(self, records, strict_only=False):
        self.close_block()
        self.block = {'records': records, 'paras': [], 'prereq': None, 'mode': 'desc', 'books': []}

    def close_block(self):
        b, self.block = self.block, None
        if not b or not b['records']:
            return
        paras = [p for p in b['paras'] if p]
        if b['prereq'] is not None:                      # strict: everything up to the prerequisite
            desc = paras
        else:                                            # loose: the first paragraph only, if real
            desc = paras[:1] if paras and len(paras[0].split()) >= 15 else []
        text = ' '.join(desc).strip()
        for r in b['records']:
            if text:
                r['desc'].append(text)
                r['kind'] = 'details' if r['kind'] in ('line', 'heading') else r['kind']
            if b['prereq'] is not None and not r['prereq']:
                r['prereq'] = b['prereq']
            r['books'] += b['books']

    # ---- the outline key table (EEE/ECE)
    def outline_row(self, key, value):
        o = self.outline
        k = key.lower()
        if k.startswith('course code'):
            o['codes'] = codes_in(value)
        elif k.startswith('credit'):
            o['credits'] = [fmt_credit(x) for x in re.findall(r'\d{1,2}(?:\.\d{1,2})?', value)]
        elif k.startswith('category'):
            o['category'] = md(value)
        elif k.startswith('prerequisite'):
            o['prereq'] = value
        elif k.startswith('equivalent'):
            o['equivalents'] = codes_in(value)

    def close_outline(self):
        o, self.outline = self.outline, None
        if not o or not o.get('codes'):
            return []
        recs = []
        creds = o.get('credits') or []
        for i, code in enumerate(o['codes']):
            cr = creds[i] if len(creds) == len(o['codes']) else (creds[0] if len(creds) == 1 else None)
            r = self.define([code], None, 'outline', cr, o.get('prereq'), o.get('category'))[0]
            # the outline's own theory + lab codes are companions, not equivalents
            r['equivalents'] = sorted(set(o.get('equivalents', [])) - set(o['codes']))
            recs.append(r)
        return recs

    # ---- main loop
    def parse(self, body):
        lines = body.split('\n')
        header, row_cat = None, ''
        for i, raw in enumerate(lines):
            line = raw.rstrip()
            s = line.strip()
            if not s:
                continue
            # ---------------- tables
            if s.startswith('|'):
                cells = [c.strip() for c in s.strip('|').split('|')]
                if all(re.fullmatch(r':?-{2,}:?', c) or not c for c in cells):
                    continue                                    # separator row
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ''
                if re.fullmatch(r'\|?\s*:?-{2,}.*', nxt) and header is None:
                    header = [md(c).lower() for c in cells]
                    if OUTLINE_KEY.match(md(cells[0])):         # outline tables have no real header
                        self.outline = self.outline or {}
                        self.outline_row(md(cells[0]), cells[1] if len(cells) > 1 else '')
                    else:
                        cat = next((md(c) for c in cells if re.search(r'core|elective|foundation|major|minor', md(c), re.I)), '')
                        header_cats = [md(c).rstrip(':') for c in cells]
                        self._header_cats = header_cats
                    continue
                if OUTLINE_KEY.match(md(cells[0])) and len(cells) >= 2:
                    self.outline = self.outline or {}
                    self.outline_row(md(cells[0]), cells[1])
                    continue
                row_cat = self.table_row(cells, header or [], row_cat)
                continue
            if header is not None or self.outline is not None:
                header, row_cat = None, ''                      # a table ended
                self._header_cats = []
                if self.outline is not None:
                    recs = self.close_outline()
                    if recs:
                        self.open_block(recs)
                        self.block['mode'] = 'outline'          # wait for "B. ... Description"
            self.text_line(s)
        self.close_block()
        if self.outline is not None:
            self.close_outline()
        return self

    _header_cats = []

    def table_row(self, cells, header, row_cat):
        col = lambda word: next((k for k, h in enumerate(header) if word in h), None)
        c_cred, c_pre = col('credit'), col('prereq')
        c_cat = next((k for k, h in enumerate(header) if h in ('stream', 'category', 'type')), None)
        texts = [md(c) for c in cells]
        # a row with no code at all but text in the first cell: a section/category row
        if not any(codes_in(c) for c in cells):
            first = next((t for t in texts if t), '')
            if first and not NUM_CELL.match(first):
                return clean_cat(first) or row_cat
            return row_cat
        if c_cat is not None and c_cat < len(texts) and texts[c_cat] and not codes_in(cells[c_cat]):
            row_cat = clean_cat(texts[c_cat]) or row_cat
        elif texts and texts[0] and not codes_in(cells[0]) and not NUM_CELL.match(texts[0]) and len(cells) > 2:
            row_cat = clean_cat(texts[0]) or row_cat
        cat = row_cat or self.heading_cat
        for j, cell in enumerate(cells):
            if j == c_pre:
                continue
            if '<br>' in cell:                                  # "**CSE 110:** Title (3 credits)<br>..."
                hcat = clean_cat(self._header_cats[j]) if j < len(self._header_cats) else ''
                for part in cell.split('<br>'):
                    codes, rest = leading_codes(part)
                    t = clean_title(rest)
                    if codes and plausible_title(t):
                        self.define(codes, t, 'list', credits_of(part), None, hcat or cat)
                continue
            codes, rest = leading_codes(cell)
            if not codes:
                found = codes_in(cell)
                if found and (OPTION_WORDS.search(cell) or len(found) >= 2):
                    label = re.split(r'[:]', md(cell))[0].strip() if ':' in cell else ''
                    self.mention(found, ' — '.join(x for x in (cat, label) if x) or cat)
                continue
            title = clean_title(rest)
            nxt = j + 1
            if not plausible_title(title) and nxt < len(cells) and not re.sub(r'[\s*]', '', rest):
                # the title is in the next cell, unless that is another course slot
                # ("| EEE 400D [2nd semester] | Program Elective V |" in a semester plan)
                inner, inner_rest = leading_codes(cells[nxt])
                if inner and len(inner) == 1:                    # | EEE101/101L | EEE 101L Title |
                    codes, title = inner, clean_title(inner_rest)
                elif not SLOT.search(md(cells[nxt])) and not codes_in(cells[nxt]):
                    title = clean_title(cells[nxt])
                nxt += 1
            if not plausible_title(title):
                return row_cat
            cr = None
            if c_cred is not None and j < c_cred < len(texts):     # a credit column before the code is a semester total
                cr = (credits_of(cells[c_cred]) or
                      (fmt_credit(NUM_CELL.match(texts[c_cred]).group(1)) if NUM_CELL.match(texts[c_cred]) else None))
            if cr is None:
                cr = credits_of(cell)
            if cr is None:
                for c in texts[nxt:]:
                    if c_pre is not None and texts.index(c) == c_pre:
                        break
                    m = NUM_CELL.match(c)
                    if m:
                        cr = fmt_credit(m.group(1))
                        break
            pre = cells[c_pre] if c_pre is not None and c_pre < len(cells) and md(cells[c_pre]) else None
            pair = split_pair(title) if len(codes) == 2 else None
            if pair and not ('/' in cell):
                # "| EEE 343 EEE 343L | DSP DSP Laboratory | (3 Credits) (1 Credit) |": theory + lab
                crs = credits_all(' '.join(texts[nxt - 1:]))
                for k, (code, part) in enumerate(zip(codes, pair)):
                    r = self.define([code], part, 'table', crs[k] if len(crs) >= 2 else cr, pre, cat)[0]
                    r['equivalents'] = []
                return row_cat
            # "Emergence of Bangladesh/ Bangladesh Studies" for "EMB 101/ DEV 101": one title each
            parts = [p.strip() for p in title.split('/')]
            if len(codes) > 1 and len(parts) == len(codes) and all(plausible_title(p) for p in parts):
                for code, part in zip(codes, parts):
                    r = self.define([code], part, 'table', cr, pre, cat)[0]
                    r['equivalents'] = [c for c in codes if c != code]
            else:
                self.define(codes, title, 'table', cr, pre, cat)
            return row_cat
        return row_cat

    def text_line(self, s):
        plain = md(s)
        b = self.block
        # prerequisite line
        pm = PREREQ.match(plain)
        if pm and (b or self.defs):
            if b:
                b['prereq'] = pm.group('t') or 'None'
                b['mode'] = 'after-prereq'
            return
        if b and b['mode'] == 'outline':
            if re.match(r'^\W*B\.\s*course\s+catalog\s+description', plain, re.I):
                b['mode'] = 'desc'
            elif re.match(r'^\W*[C-Z]\.\s', plain):
                self.close_block()
            return
        if BOOKS.match(plain) and b:
            b['mode'] = 'books'
            return
        if STOP.match(plain) and b:
            b['mode'] = 'stopped'
            return
        if DESC_HEAD.match(plain) and b and b['mode'] == 'desc' and len(plain.split()) <= 4:
            return                                       # "**Course Overview**" inside a block
        # a course definition at the start of the line
        lead = LEAD.match(s).group(0)
        body = s[len(lead):]
        codes, rest = leading_codes(body)
        if codes:
            title = clean_title(rest)
            if plausible_title(title):
                kind = 'heading' if (body.startswith('**') or lead.strip().startswith('#')) else 'line'
                kind = 'list' if re.match(r'^\s*(?:[●•▪·◦\-–]|\d{1,3}[.)])', s) else kind
                parts = [p.strip() for p in title.split('/')]
                if len(codes) > 1 and len(parts) == len(codes) and all(plausible_title(p) for p in parts):
                    recs = []                                    # "ANT 202 / SOC 201: A / B": one title each
                    for code, part in zip(codes, parts):
                        r = self.define([code], part, kind, credits_of(rest))[0]
                        r['equivalents'] = [c for c in codes if c != code]
                        recs.append(r)
                else:
                    recs = self.define(codes, title, kind, credits_of(rest))
                self.open_block(recs)
                return
        found = codes_in(s)
        if found and not codes and (OPTION_WORDS.search(s)) and len(found) >= 2:
            self.mention(found, self.heading_cat)
        # headings / bold lines without a code: a new category
        if s.startswith('#') or (s.startswith('**') and s.rstrip().endswith('**') and len(plain.split()) <= 10
                                 and not DESC_HEAD.match(plain)):
            if b and b['mode'] == 'desc' and not b['paras'] and DESC_HEAD.match(plain):
                return
            self.close_block()
            if not codes_in(s):
                self.heading_cat = clean_cat(plain) or self.heading_cat
            return
        if not b:
            return
        if b['mode'] == 'books':
            if len(b['books']) < 8 and len(plain) > 8:
                b['books'].append(re.sub(r'^\s*(?:\d{1,2}[.)]|[●•▪·◦\-–*])\s*', '', plain)[:200])
            return
        if b['mode'] == 'desc':
            b['paras'].append(plain)


# ---------------------------------------------------------------- merging
def rank(rec, doc_type, doc_title):
    """Title sources, best first: updated degree plans, course details, course lists, other pages."""
    if doc_type == 'degree_plan':
        return 0
    if not PROGRAM_TITLE.search(doc_title or ''):
        return 3
    return 1 if rec['kind'] in ('details', 'outline', 'heading') else 2


def cluster_titles(items):
    """items: [(title, rank, doc)] -> clusters [[(title, rank, doc)]], best cluster first.
    Spelling differences and "one title extends the other" share a cluster."""
    clusters = []
    for it in sorted(items, key=lambda x: (x[1], -len(x[0]))):
        n = norm_title(it[0])
        for cl in clusters:
            for t, _, _ in cl:
                m = norm_title(t)
                if (n == m or n.startswith(m + ' ') or m.startswith(n + ' ')
                        or difflib.SequenceMatcher(None, n, m).ratio() > 0.85):
                    cl.append(it)
                    break
            else:
                continue
            break
        else:
            clusters.append([it])
    return sorted(clusters, key=lambda cl: (min(x[1] for x in cl), -len(cl)))


def best_title(cluster):
    """The most complete title of a cluster (longest; not all-caps if possible)."""
    return max(cluster, key=lambda x: (not x[0].isupper(), len(norm_title(x[0])), -x[1]))[0]


KIND_LABEL = {'table': 'course list', 'list': 'course list', 'line': 'course list', 'heading': 'course list',
              'details': 'course details', 'outline': 'course outline'}


def build(docs):
    """docs: [(doc_id, title, doc_type, body)] -> {code: card}"""
    defs, mentions, titles = [], [], {}
    for doc_id, title, doc_type, body in docs:
        p = DocParser(doc_id, title, doc_type).parse(body)
        for r in p.defs:
            r['rank'] = rank(r, doc_type, title)
        defs += p.defs
        mentions += p.mentions
        titles[doc_id] = (title, doc_type)
    by_code = defaultdict(list)
    for r in defs:
        by_code[r['code']].append(r)
    cards = {}
    for code in sorted(set(by_code) | {m['code'] for m in mentions}):
        recs = by_code.get(code, [])
        title_items = [(r['title'], r['rank'], r['doc']) for r in recs
                       if r['title'] and r['doc'] not in NO_TITLES_FROM and plausible_title(r['title'])]
        clusters = cluster_titles(title_items)
        card = {'code': code, 'prefix': code.split()[0], 'level': level_of(code),
                'title': best_title(clusters[0]) if clusters else None,
                'also_listed_as': [], 'title_variants': [], 'credits': [], 'prerequisites': [],
                'equivalents': [], 'programs': [], 'options': [], 'descriptions': [], 'books': [],
                'sources': [], 'minimal': not recs}
        if clusters:
            seen = {norm_title(card['title'])}
            for t, _, _ in clusters[0]:
                if norm_title(t) not in seen and t.lower() != card['title'].lower():
                    card['also_listed_as'].append(t)
                    seen.add(norm_title(t))
            for cl in clusters[1:]:
                card['title_variants'].append({
                    'title': best_title(cl),
                    'sources': sorted({f"{titles[d][0]} ({KIND_LABEL.get(next(r['kind'] for r in recs if r['doc'] == d), 'list')})"
                                       for _, _, d in cl})})
        cred = defaultdict(set)
        for r in recs:
            if r['credits'] is not None:
                cred[r['credits']].add(f"{titles[r['doc']][0]} ({KIND_LABEL.get(r['kind'], 'course list')})")
        card['credits'] = [{'value': v, 'sources': sorted(s)} for v, s in
                           sorted(cred.items(), key=lambda kv: -len(kv[1]))]
        pre = {}
        for r in recs:
            if r['prereq']:
                info = prereq_info(r['prereq'])
                key = info['text'].lower()
                pre.setdefault(key, {**info, 'sources': set()})['sources'].add(titles[r['doc']][0])
        card['prerequisites'] = [{**v, 'sources': sorted(v['sources'])} for v in pre.values()]
        card['equivalents'] = sorted({e for r in recs for e in r['equivalents'] if e != code})
        progs = defaultdict(set)
        for r in recs:
            cat = clean_cat(r['category'] or '')
            if cat and cat.lower() not in titles[r['doc']][0].lower():
                progs[r['doc']].add(cat)
            else:
                progs[r['doc']]
        card['programs'] = [{'program': titles[d][0], 'doc_id': d, 'categories': sorted(c)}
                            for d, c in sorted(progs.items(), key=lambda kv: titles[kv[0]][0])
                            if PROGRAM_TITLE.search(titles[d][0])]
        opts = defaultdict(set)
        for m in mentions:
            if m['code'] == code:
                opts[m['doc']].add(' — '.join(x for x in (clean_cat(p) for p in (m['category'] or '').split(' — ')) if x))
        card['options'] = [{'program': titles[d][0], 'doc_id': d, 'categories': sorted(c for c in cs if c)}
                           for d, cs in sorted(opts.items())]
        descs = []
        for r in sorted(recs, key=lambda r: r['rank']):
            for t in r['desc']:
                words = t.split()
                t = ' '.join(words[:220]) + (' …' if len(words) > 220 else '')
                if not any(difflib.SequenceMatcher(None, t[:300].lower(), d['text'][:300].lower()).ratio() > 0.8
                           for d in descs):
                    descs.append({'text': t, 'source': titles[r['doc']][0]})
        card['descriptions'] = descs[:2]
        books = []
        for r in recs:
            for bk in r['books']:
                if bk not in books:
                    books.append(bk)
        card['books'] = books[:6]
        card['sources'] = sorted({r['doc'] for r in recs} | {m['doc'] for m in mentions if m['code'] == code})
        cards[code] = card
    return cards


# ---------------------------------------------------------------- writing
def card_markdown(c):
    title = c['title'] or 'title not given in the documents'
    lines = [f"## {c['code']} — {title}", '',
             f"- **Course code:** {c['code']} · department prefix {c['prefix']} · {c['level']}"]
    if c['minimal']:
        where = '; '.join(f"{o['program']}" + (f" under {', '.join(o['categories'])}" if o['categories'] else '')
                          for o in c['options'])
        lines.append(f"- **Listed in:** {where}. No title, credits or description in the documents.")
    if c['also_listed_as']:
        lines.append(f"- **Also listed as:** {'; '.join(c['also_listed_as'])}")
    if c['title_variants']:
        lines.append('- **Other titles in the documents (they differ):** ' + '; '.join(
            f"{v['title']} ({', '.join(v['sources'])})" for v in c['title_variants']))
    if c['credits']:
        if len(c['credits']) == 1:
            lines.append(f"- **Credits:** {c['credits'][0]['value']}")
        else:
            lines.append('- **Credits (the documents differ):** ' + '; '.join(
                f"{v['value']} ({', '.join(v['sources'])})" for v in c['credits']))
    if c['prerequisites']:
        lines.append('- **Prerequisites:** ' + '; '.join(
            p['text'] + (f" ({', '.join(p['sources'])})" if len(c['prerequisites']) > 1 else '')
            for p in c['prerequisites']))
    if c['equivalents']:
        lines.append(f"- **Equivalent / cross-listed:** {', '.join(c['equivalents'])}")
    if c['programs']:
        lines.append('- **Programs:** ' + '; '.join(
            p['program'] + (f" — {', '.join(p['categories'])}" if p['categories'] else '') for p in c['programs']))
    if c['options'] and not c['minimal']:
        lines.append('- **Listed as an option in:** ' + '; '.join(
            o['program'] + (f" — {', '.join(o['categories'])}" if o['categories'] else '') for o in c['options']))
    for d in c['descriptions']:
        lines.append(f"- **Description ({d['source']}):** {d['text']}")
    if c['books']:
        lines.append('- **Suggested books:** ' + ' | '.join(c['books']))
    lines.append(f"- **Source documents:** {', '.join(c['sources'])}")
    return '\n'.join(lines) + '\n'


def write(cards):
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    by_prefix = defaultdict(list)
    for c in cards.values():
        by_prefix[c['prefix']].append(c)
    for prefix, cs in sorted(by_prefix.items()):
        head = (f'---\ndoc_id: "course_catalog_{prefix.lower()}"\n'
                f'title: "Course catalogue: {prefix} courses"\nstatus: "current"\nrag_priority: 1\n'
                f'category: "courses"\ndoc_type: "course_catalog"\nsource_type: "compiled"\n'
                f'source: "compiled by scripts/build_course_catalog.py from the program pages in BRACU_RAG_Corpus"\n'
                f'approval_authority: ""\napproved_on: ""\neffective_date: ""\nsupersedes: []\n'
                f'superseded_by: []\nsuperseded_sections: []\nrelated: []\n'
                f'notes: "GENERATED by scripts/build_course_catalog.py: do not edit by hand. Rerun it, then '
                f'scripts/ingest.py, whenever program pages change."\n---\n\n'
                f'# Course catalogue: {prefix} courses\n\n'
                f'> Generated by `scripts/build_course_catalog.py` from the program pages. Do not edit by hand.\n\n')
        body = '\n'.join(card_markdown(c) for c in sorted(cs, key=lambda c: c['code']))
        (OUT_DIR / f'{prefix}.md').write_text(head + body, encoding='utf-8')
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding='utf-8')
    return len(by_prefix)


def load_docs():
    docs = []
    for f in sorted(CORPUS.rglob('*.md')):
        rel = f.relative_to(CORPUS)
        if (any(p.startswith('_duplicates') for p in rel.parts) or f.name == 'README.md'
                or OUT_DIR in f.parents):
            continue
        meta, body = read_doc(str(f))
        if meta.get('doc_id'):
            docs.append((meta['doc_id'], meta.get('title', ''), meta.get('doc_type', ''), body))
    return docs


def report(cards):
    n = len(cards)
    has = lambda k: sum(1 for c in cards.values() if c[k])
    full = [c for c in cards.values() if not c['minimal']]
    print(f"{n} cards ({len(full)} defined, {n - len(full)} mention-only) | titles {has('title')} | "
          f"credits {has('credits')} | prerequisites {has('prerequisites')} | descriptions {has('descriptions')} | "
          f"books {has('books')} | equivalents {has('equivalents')}")
    print(f"title variants kept: {sum(1 for c in full if c['title_variants'])} | "
          f"credit variants kept: {sum(1 for c in full if len(c['credits']) > 1)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true', help='parse and report, write nothing')
    args = ap.parse_args()
    cards = build(load_docs())
    report(cards)
    if not args.check:
        files = write(cards)
        print(f'wrote {files} files to {OUT_DIR} and {OUT_JSON}')


if __name__ == '__main__':
    main()
