"""Import an official BRAC University class schedule PDF into Neon (class_schedule).

    python scripts/import_class_schedule.py --pdf "data/schedules/Class Schedule Fall 2026.pdf" \
        --term "Fall 2026" --valid-from 2026-09-01 --valid-until 2027-01-31 \
        --check-csv data/schedules/class_schedule_fall_2026.csv --dry-run

The PDF is one table over many pages: SL | Course | Faculty | Section | Day |
Start Time | End Time | Room. Every page repeats the header row and the SL 1 row,
so rows are kept once per SL, and the SL numbers must run 1..N without a gap.
One row per meeting day. Labs are separate codes ending in L after the number
(CSE220L, CSE490BL; TESL501 is not a lab) with their own section numbers.

Normalised: course "CSE220L" -> course_norm "CSE 220L", base_course "CSE 220",
is_lab; section "01 (BBA)" / "11-LLB" / "10- BDM" -> number "01" + tag; "8" -> "08";
day -> "Sunday" + day_order (Saturday 1 ... Friday 7); times -> time; a room cell
with day-specific rooms ("SUN 2:00PM: 07A-08C; TUE 2:00PM: 09G-31T") -> the room
for the row's day; blank faculty -> NULL.

--check-csv compares every row with a reference extraction (columns sl, course,
faculty, section, day, start, end, room) and stops with a report on any difference.
Without --dry-run the term's rows and its schedule_terms row are replaced in one
transaction; other terms are not touched.

Needs pdfplumber (requirements-sync.txt) and DATABASE_URL.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HEADER = ['SL', 'Course', 'Faculty', 'Section', 'Day', 'Start Time', 'End Time', 'Room']
DAYS = ['SATURDAY', 'SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY']
COURSE = re.compile(r'^([A-Z]{2,4})(\d{3})([A-Z]{0,2})$')
SECTION = re.compile(r'^(\d{1,2})\s*(?:\(\s*([^()]+?)\s*\)|-\s*(\S.*?))?$')
TIME = re.compile(r'^(\d{1,2}):(\d{2})\s*([AP]M)$', re.I)
DAY_ROOM = re.compile(r'^(SAT|SUN|MON|TUE|WED|THU|FRI)\s+(\d{1,2}:\d{2}\s*[AP]M)\s*:\s*(\S+)$', re.I)


class BadRow(Exception):
    pass


def clean(cell):
    return re.sub(r'\s+', ' ', cell or '').strip()


def parse_time(s):
    m = TIME.match(clean(s).upper())
    if not m:
        raise BadRow(f'time {s!r}')
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    h = h % 12 + (12 if ap == 'PM' else 0)
    return datetime(2000, 1, 1, h, mi).time()


def parse_course(raw):
    code = clean(raw).upper().replace(' ', '')
    m = COURSE.match(code)
    if not m:
        raise BadRow(f'course {raw!r}')
    prefix, num, suffix = m.groups()
    is_lab = suffix.endswith('L')
    base_suffix = suffix[:-1] if is_lab else suffix
    return {'course': code, 'course_norm': f'{prefix} {num}{suffix}', 'is_lab': is_lab,
            'base_course': f'{prefix} {num}{base_suffix}'}


def parse_section(raw):
    m = SECTION.match(clean(raw))
    if not m:
        raise BadRow(f'section {raw!r}')
    return m.group(1).zfill(2), (m.group(2) or m.group(3) or None)


def room_for_day(raw, day, start):
    """The room cell as is, or, when it lists rooms per day, the one for this day."""
    cell = clean(raw)
    if ':' not in cell:
        return cell, False
    parts = [DAY_ROOM.match(p.strip()) for p in cell.split(';')]
    if not all(parts):
        raise BadRow(f'room {raw!r}')
    same_day = [p for p in parts if p.group(1).upper() == day[:3]]
    if len(same_day) > 1:
        same_day = [p for p in same_day if parse_time(p.group(2)) == start]
    if len(same_day) != 1:
        raise BadRow(f'room {raw!r}: no single room for {day}')
    return same_day[0].group(3), True


def normalise(sl, course, faculty, section, day, start, end, room):
    """One schedule row (strings as in the PDF/CSV) -> the database fields."""
    d = clean(day).upper()
    if d not in DAYS:
        raise BadRow(f'day {day!r}')
    number, tag = parse_section(section)
    st, et = parse_time(start), parse_time(end)
    r, day_specific = room_for_day(room, d, st)
    return {'sl': int(sl), **parse_course(course), 'section_number': number, 'section_tag': tag,
            'day': d.title(), 'day_order': DAYS.index(d) + 1, 'start_time': st, 'end_time': et,
            'room': r, 'faculty': clean(faculty) or None, 'day_specific_room': day_specific}


def raw_rows_from_tables(tables):
    """SL -> the 8 raw cells, from extracted page tables (header and repeated rows dropped).
    Raises BadRow when the same SL appears twice with different cells."""
    out = {}
    for table in tables:
        for cells in table:
            cells = [clean(c) for c in cells]
            if len(cells) != 8 or not cells[0].isdigit():
                continue                          # title row, header row
            sl = int(cells[0])
            if sl in out and out[sl] != cells:
                raise BadRow(f'SL {sl} appears twice with different cells: {out[sl]} / {cells}')
            out[sl] = cells
    return out


def read_pdf(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        tables = []
        for page in pdf.pages:
            tables += page.extract_tables()
    return raw_rows_from_tables(tables)


def build_rows(raw):
    """Normalised rows sorted by SL; stops if SLs are not exactly 1..N."""
    sls = sorted(raw)
    missing = sorted(set(range(1, sls[-1] + 1)) - set(sls)) if sls else []
    if not sls or sls[0] != 1 or missing:
        raise BadRow(f'SL numbers are not 1..{sls[-1] if sls else 0}: missing {missing[:20]}')
    rows, errors = [], []
    for sl in sls:
        try:
            rows.append(normalise(*raw[sl]))
        except BadRow as e:
            errors.append(f'SL {sl}: {e}')
    if errors:
        raise BadRow('cannot read these rows:\n  ' + '\n  '.join(errors[:30]))
    return rows


def check_csv(raw, csv_path):
    """Differences between the PDF cells and the reference CSV, as report lines."""
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        ref = {int(r['sl']): [clean(r[k]) for k in ('sl', 'course', 'faculty', 'section', 'day', 'start',
                                                     'end', 'room')] for r in csv.DictReader(f)}
    diffs = []
    for sl in sorted(set(raw) | set(ref)):
        a, b = raw.get(sl), ref.get(sl)
        if a is None or b is None:
            diffs.append(f'SL {sl}: only in the {"CSV" if a is None else "PDF"}')
            continue
        for name, x, y in zip(HEADER, a, b):
            if x != y:
                diffs.append(f'SL {sl} {name}: PDF {x!r} vs CSV {y!r}')
    return diffs


def summary(rows):
    pairs = {(r['course'], r['section_number'], r['section_tag']) for r in rows}
    return {
        'rows': len(rows),
        'courses': len({r['course'] for r in rows}),
        'sections (course + section as printed)': len(pairs),
        'lab courses': len({r['course'] for r in rows if r['is_lab']}),
        'lab sections': len({p for p in pairs if p[0] in {r['course'] for r in rows if r['is_lab']}}),
        'tagged sections': len({p for p in pairs if p[2]}),
        'rows with day-specific rooms': sum(r['day_specific_room'] for r in rows),
        'rows with blank faculty': sum(r['faculty'] is None for r in rows),
        'rows on Friday': sum(r['day'] == 'Friday' for r in rows),
    }


def lab_mismatches(rows):
    """Lab sections whose number has no theory section of the base course."""
    theory = {(r['base_course'], r['section_number']) for r in rows if not r['is_lab']}
    labs = {(r['base_course'], r['course'], r['section_number']) for r in rows if r['is_lab']}
    return sorted((c, s) for b, c, s in labs if (b, s) not in theory)


COLS = ['term', 'sl', 'course', 'course_norm', 'is_lab', 'base_course', 'section_number', 'section_tag',
        'day', 'day_order', 'start_time', 'end_time', 'room', 'faculty', 'source_file']


def store(rows, term, valid_from, valid_until, source_file, url=None):
    """Replace the term's rows and its schedule_terms row in one transaction."""
    from bracu import config
    from bracu.db import connect
    url = url or os.environ.get('DATABASE_URL_UNPOOLED') or config.DATABASE_URL
    with connect(url) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute((ROOT / 'sql' / 'schema.sql').read_text(encoding='utf-8'))
            cur.execute('DELETE FROM class_schedule WHERE term = %s', (term,))
            cur.executemany(f"INSERT INTO class_schedule ({', '.join(COLS)}) VALUES "
                            f"({', '.join(['%s'] * len(COLS))})",
                            [[term] + [r[c] for c in COLS[1:-1]] + [source_file] for r in rows])
            cur.execute("""INSERT INTO schedule_terms (term, valid_from, valid_until, source_file, row_count)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (term) DO UPDATE SET valid_from = EXCLUDED.valid_from,
                               valid_until = EXCLUDED.valid_until, source_file = EXCLUDED.source_file,
                               row_count = EXCLUDED.row_count, imported_at = now()""",
                        (term, valid_from, valid_until, source_file, len(rows)))
        n = conn.execute('SELECT count(*) FROM class_schedule WHERE term = %s', (term,)).fetchone()[0]
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--pdf', required=True)
    ap.add_argument('--term', required=True, help='e.g. "Fall 2026"')
    ap.add_argument('--valid-from', required=True, type=date.fromisoformat)
    ap.add_argument('--valid-until', required=True, type=date.fromisoformat)
    ap.add_argument('--check-csv', help='reference extraction to compare every row with')
    ap.add_argument('--dry-run', action='store_true', help='parse and check only, write nothing')
    args = ap.parse_args()
    if args.valid_until < args.valid_from:
        sys.exit('--valid-until is before --valid-from')
    pdf = Path(args.pdf)
    try:
        raw = read_pdf(pdf)
        if args.check_csv:
            diffs = check_csv(raw, args.check_csv)
            if diffs:
                sys.exit(f'Stopped: {len(diffs)} difference(s) between the PDF and {args.check_csv}:\n  '
                         + '\n  '.join(diffs[:50]) + ('\n  ...' if len(diffs) > 50 else ''))
            print(f'check: all {len(raw)} rows match {args.check_csv}')
        rows = build_rows(raw)
    except BadRow as e:
        sys.exit(f'Stopped: {e}')
    print(f'{args.term} from {pdf.name} (valid {args.valid_from} to {args.valid_until}):')
    for k, v in summary(rows).items():
        print(f'  {k:40s} {v}')
    mism = lab_mismatches(rows)
    print(f'  {"lab sections without a theory section":40s} {len(mism)}')
    if args.dry_run:
        print('dry run: nothing written')
        return
    n = store(rows, args.term, args.valid_from, args.valid_until, pdf.name)
    print(f'stored {n} rows for {args.term} in class_schedule (other terms untouched)')


if __name__ == '__main__':
    main()
