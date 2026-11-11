"""Facts for the degree-cost card (bracu/degree_cost.py), read from the corpus.

    python scripts/build_degree_costs.py              # parse and print the facts
    python scripts/build_degree_costs.py --load-db    # also store them in Postgres

Every number comes from a document and is stored with the sentence or table row
it was read from (quote) and the document's id, title and link. If a pattern no
longer matches (the page changed), the script stops instead of guessing.

What is read
- Tuition and Fees page (undergraduate fees for new students enrolled from Spring
  2025): admission application fee, admission fee, library membership fee,
  tuition per credit and semester fee per program group, Residential Semester fee.
- Each bachelor's program page: total credits (LLB states no total: the credits of
  its 8-semester plan are added up, and the card says so), and the length of study
  where the page states it.
- Semesters a year: BRAC University runs 3 (Spring, Summer, Fall); Pharmacy and
  LLB are four-year programs of 8 semesters (2 a year).

Output: data/degree_costs.json; with --load-db the same JSON in the course_catalog
table under key 'degree_cost' (sql/schema.sql), which the app reads.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bracu import config                       # noqa: E402

CORPUS = ROOT / 'BRACU_RAG_Corpus'
OUT_JSON = ROOT / 'data' / 'degree_costs.json'
FEES_DOC = '03_website_pages/admissions/web_tuition_and_fees.md'
ACAD = '03_website_pages/academics/'

# fee groups as the Tuition and Fees table names them
FEE_GROUP_ROWS = {'PHR': 'Pharmacy', 'LAW': 'Law', 'ARC': 'ARC', 'CSE': 'CSE', 'OTHER': 'All other Program'}

# key, file, fee group, names (for the question), credits pattern, length pattern (or None)
PROGRAMS = [
    ('ARC', 'web_bachelor_of_architecture_arc.md', 'ARC',
     ['Bachelor of Architecture', 'Architecture', 'B.Arch', 'BArch'],
     r'total credit requirement for the degree of Bachelor of Architecture is (\d+) credits',
     r'length of study for the degree is recommended to be (\d+) years \((\d+) semesters\)'),
    ('AELS', 'web_bachelor_of_arts_in_applied_english_language_studies_ba_in_aels.md', 'OTHER',
     ['Bachelor of Arts in Applied English Language Studies', 'Applied English Language Studies',
      'BA in AELS', 'English'],
     r'(\d+)-credit Bachelor of Arts in Applied English',
     r'(\d+)-year, (\d+)-semester, \d+-credit Bachelor of Arts'),
    ('BDM', 'web_bachelor_of_disaster_management_bdm.md', 'OTHER',
     ['Bachelor of Disaster Management', 'Disaster Management'],
     r'Total Credits:\s*(\d+) credits', None),
    ('LLB', 'web_bachelor_of_laws_ll_b_hons_llb.md', 'LAW',
     ['Bachelor of Laws', 'LL.B.', 'LL.B', 'Law'],
     None,                                              # no total stated: the plan is added up
     r'(four)-year undergraduate program'),
    ('PHR', 'web_bachelor_of_pharmacy_hons_phr.md', 'PHR',
     ['Bachelor of Pharmacy', 'B.Pharm', 'Pharmacy'],
     r'total credit requirement for the degree of Bachelor of Pharmacy \(Hons\.\) is (\d+)',
     r'The (four)-year pharmacy undergraduate program has (eight) semesters'),
    ('CS', 'web_bachelor_of_science_in_computer_science_cs.md', 'OTHER',
     ['Bachelor of Science in Computer Science', 'Computer Science'],
     r'rigorous (\d+) credit program', None),
    ('CSE', 'web_bachelor_of_science_in_computer_science_engineering_cse.md', 'CSE',
     ['Bachelor of Science in Computer Science & Engineering',
      'Bachelor of Science in Computer Science and Engineering', 'Computer Science and Engineering',
      'Computer Science & Engineering'],
     r'demanding (\d+) credit program', None),
    ('EEE', 'web_bachelor_of_science_in_electrical_and_electronic_engineering_eee.md', 'OTHER',
     ['Bachelor of Science in Electrical and Electronic Engineering', 'Electrical and Electronic Engineering'],
     r'require completion of (\d+) credit hours', None),
    ('ECE', 'web_bachelor_science_electronic_and_communication_engineering_ece.md', 'OTHER',
     ['Bachelor of Science in Electronic and Communication Engineering',
      'Electronic and Communication Engineering'],
     r'require completion of (\d+) credit hours', None),
    ('MAT', 'web_bachelor_of_science_in_mathematics_mat.md', 'OTHER',
     ['Bachelor of Science in Mathematics', 'Mathematics', 'Math'],
     r'total credit requirement for the degree of Bachelor of Science in Mathematics is (\d+)', None),
    ('MIC', 'web_bachelor_of_science_in_microbiology_mic.md', 'OTHER',
     ['Bachelor of Science in Microbiology', 'Microbiology'],
     r'BS in Microbiology\*\* \((\d+) credits\)',
     r'A (\d+)-year \((\d+) semesters\) Microbiology undergraduate programme'),
    ('PHY', 'web_bachelor_science_physics_phy.md', 'OTHER',
     ['Bachelor of Science in Physics', 'Physics'],
     r'total credit requirements for the degree of Bachelor of Science in Physics is (\d+)', None),
    ('APE', 'web_bachelor_of_science_in_applied_physics_and_electronics_ape.md', 'OTHER',
     ['Bachelor of Science in Applied Physics and Electronics', 'Applied Physics and Electronics'],
     r'total credit requirement for BS in APE degree is (\d+)', None),
    ('ECO', 'web_bachelor_of_social_science_in_economics_eco.md', 'OTHER',
     ['Bachelor of Social Science in Economics', 'Economics'],
     r'\| Total \| \*\*\d+\*\* \| \*\*(\d+)\*\* \|', None),
    ('ANT', 'web_bachelor_of_social_sciences_in_anthropology_ant.md', 'OTHER',
     ['Bachelor of Social Sciences in Anthropology', 'Anthropology'],
     r'\*\*Total Credits\*\* \| \*\*(\d+)\*\*', None),
]
# BRAC University's three semesters a year, as the documents say it
CALENDAR = [(ACAD + 'web_master_of_arts_in_teaching_english_to_speakers_of_other_languages_ma_in_tesol.md',
             r'We have (3) semesters a year: Spring, Summer and Fall\.')]
TWO_A_YEAR = {'PHR', 'LLB'}
WORDS = {'four': 4, 'five': 5, 'eight': 8}


class Missing(Exception):
    pass


def read_doc(rel):
    text = (CORPUS / rel).read_text(encoding='utf-8')
    front = {}
    m = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(':')
            front[k.strip()] = v.strip().strip('"')
    return text, {'doc_id': front.get('doc_id', ''), 'title': front.get('title', ''),
                  'url': front.get('source', '')}


def quote_at(text, start, end):
    """The sentence or table row around a match, cleaned for display."""
    line_start = text.rfind('\n', 0, start) + 1
    line_end = text.find('\n', end)
    line = text[line_start:line_end if line_end >= 0 else len(text)]
    if line.strip().startswith('|') and line.count('|') == 2:          # a one-cell table row
        line = ' ' + line.strip()[1:-1] + ' '                          # same positions
    if not line.lstrip().startswith('|'):
        s, e = start - line_start, end - line_start
        # sentence ends: ". " after a small letter, digit or bracket ("LL.B. Honours" is not one)
        ends = [m.end() for m in re.finditer(r'(?<=[a-z0-9)\]])\.\s+(?=[A-Z"“])', line)]
        a = max([x for x in ends if x <= s], default=0)
        b = min([x for x in ends if x >= e], default=len(line))
        line = line[a:b]
        s, e = s - a, e - a
        if len(line) > 220:                              # a long sentence: the part around the number
            lo, hi = max(0, s - 90), min(len(line), e + 90)
            part = line[lo:hi]
            part = part.split(' ', 1)[1] if lo and ' ' in part else part      # whole words only
            part = part.rsplit(' ', 1)[0] if hi < len(line) and ' ' in part else part
            line = ('…' if lo else '') + part.strip() + ('…' if hi < len(line) else '')
    return re.sub(r'\s+', ' ', line.replace('**', '')).strip().rstrip('*')


def find(text, pattern, what):
    m = re.search(pattern, text)
    if not m:
        raise Missing(f'{what}: pattern not found ({pattern})')
    return m, quote_at(text, m.start(), m.end())


def number(s):
    s = s.strip().lower()
    return WORDS[s] if s in WORDS else int(s.replace(',', ''))


def parse_fees():
    text, doc = read_doc(FEES_DOC)
    rows = {}
    for m in re.finditer(r'^\| \*\*([^|*]+)\*\* \| ([^|]+?) \| ([\d,]+) \|$', text, re.M):
        rows[(m.group(1).strip(), m.group(2).strip())] = (int(m.group(3).replace(',', '')),
                                                          quote_at(text, m.start(), m.end()))

    def row(fee, program):
        if (fee, program) not in rows:
            raise Missing(f'fee table row not found: {fee} / {program}')
        v, q = rows[(fee, program)]
        return {'value': v, 'quote': q}

    def amount(pattern, what):
        m, q = find(text, pattern, what)
        return {'value': int(m.group(1).replace(',', '')), 'quote': q}

    for_whom, _ = find(text, r'Undergraduate fees for new students enrolled from ([A-Za-z]+ ?-?\s?\d{4})', 'fee scope')
    return {
        **doc,
        'applies_to': 'new undergraduate students enrolled from ' + re.sub(r'\s*-\s*', ' ', for_whom.group(1)),
        'application': amount(r'Admission Application Fee Tk\. ([\d,]+)/-\s+Non-Refundable', 'application fee'),
        'admission': row('Admission Fee', 'All Program'),
        'library': amount(r'\| \*\*Library Membership fee\*\* \| Tk\.([\d,]+)/-', 'library fee'),
        'rs': row('Residential Semester Fee', 'All Program'),
        'per_credit': {'PHR': row('Tuition Fee (per credit)', 'Pharmacy'),
                       'LAW': row('Tuition Fee (per credit)', 'Law'),
                       'ARC_STUDIO': row('Tuition Fee (per credit)', 'ARC (Studio Course Fee)'),
                       'ARC': row('Tuition Fee (per credit)', 'ARC (Lecture Course Fee)'),
                       'CSE': row('Tuition Fee (per credit)', 'CSE'),
                       'OTHER': row('Tuition Fee (per credit)', 'All other Program')},
        'semester_fee': {g: row('Semester Fee', name) for g, name in FEE_GROUP_ROWS.items()},
        'remedial_course': amount(r'will incur a fee of Tk\. ([\d,]+) \(all program except LAW\)',
                                  'remedial course fee'),
        'remedial_course_law': amount(r'In case of LAW Tk\. ([\d,]+) will be incurred', 'law remedial course fee'),
        'may_change': find(text, r'Fees can be changed as per the decision of the university', 'change note')[1],
    }


def llb_plan_credits(text):
    """Credits of the 8 semesters of the LLB plan ("• LAW101 ... – 03 Credits")."""
    parts = re.split(r'\*\*(\d)(?:st|nd|rd|th) Semester\*\*', text)
    seen, total = [], 0.0
    for i in range(1, len(parts), 2):
        n = int(parts[i])
        if n in seen:                    # the plan ends where the numbering starts again
            break
        seen.append(n)
        total += sum(float(x) for x in re.findall(r'[–-]\s*0?(\d+(?:\.\d+)?)\s*Credits?', parts[i + 1]))
    if seen != list(range(1, 9)):
        raise Missing(f'LLB plan: semesters {seen}, expected 1-8')
    return int(total) if total == int(total) else total, len(seen)


def parse_programs():
    out = {}
    for key, file, group, names, credits_pat, length_pat in PROGRAMS:
        text, doc = read_doc(ACAD + file)
        p = {'key': key, 'name': doc['title'].split(' (')[0] if key != 'LLB' else doc['title'],
             'names': names, 'fee_group': group, 'doc': doc,
             'per_year': 2 if key in TWO_A_YEAR else 3}
        if credits_pat:
            m, q = find(text, credits_pat, f'{key} credits')
            p['credits'] = {'value': int(m.group(1)), 'how': 'stated', 'quote': q}
        else:
            total, n = llb_plan_credits(text)
            p['credits'] = {'value': total, 'how': 'plan',
                            'quote': f'The {n}-semester course plan on the program page lists {total} '
                                     f'credits; the page states no total.'}
        if length_pat:
            m, q = find(text, length_pat, f'{key} length')
            years = number(m.group(1))
            semesters = number(m.group(2)) if m.lastindex and m.lastindex >= 2 else years * p['per_year']
            if semesters != years * p['per_year']:
                raise Missing(f'{key}: {semesters} semesters in {years} years does not match '
                              f'{p["per_year"]} a year')
            p['length'] = {'years': years, 'semesters': semesters, 'how': 'stated', 'quote': q}
        else:
            p['length'] = {'years': 4, 'semesters': 4 * p['per_year'], 'how': 'standard'}
        out[key] = p
    return out


def parse_calendar():
    rel, pat = CALENDAR[0]
    text, doc = read_doc(rel)
    m, q = find(text, pat, 'semesters a year')
    return {'per_year': int(m.group(1)), 'quote': q, 'doc': doc}


def build():
    return {'built': date.today().isoformat(), 'fees': parse_fees(), 'calendar': parse_calendar(),
            'programs': parse_programs()}


def load_db(data, url=None):
    import os
    from bracu.db import connect
    url = url or os.environ.get('DATABASE_URL_UNPOOLED') or config.DATABASE_URL
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute((ROOT / 'sql' / 'schema.sql').read_text(encoding='utf-8'))
        cur.execute("""INSERT INTO course_catalog (key, value) VALUES ('degree_cost', %s)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                    (json.dumps(data, ensure_ascii=False),))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--load-db', action='store_true', help='store the facts in Postgres (course_catalog)')
    args = ap.parse_args()
    try:
        data = build()
    except Missing as e:
        sys.exit(f'Stopped: {e}. Check the document and the pattern in this script.')
    f = data['fees']
    print(f"Fees ({f['applies_to']}): application {f['application']['value']}, admission "
          f"{f['admission']['value']}, library {f['library']['value']}, RS {f['rs']['value']}")
    print('  per credit ' + ', '.join(f"{g} {v['value']}" for g, v in f['per_credit'].items()))
    print('  semester   ' + ', '.join(f"{g} {v['value']}" for g, v in f['semester_fee'].items()))
    print(f"Semesters a year: {data['calendar']['per_year']}")
    for k, p in data['programs'].items():
        print(f"  {k:5s} {p['credits']['value']:>4} credits ({p['credits']['how']}) | "
              f"{p['length']['semesters']:>2} semesters / {p['length']['years']} years ({p['length']['how']}) "
              f"| fees: {p['fee_group']}")
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'wrote {OUT_JSON.relative_to(ROOT)}')
    if args.load_db:
        load_db(data)
        print("stored in course_catalog (key 'degree_cost')")


if __name__ == '__main__':
    main()
