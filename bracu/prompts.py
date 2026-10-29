"""System prompt and how retrieved passages are shown to the model."""
import re
from datetime import date, datetime

from .config import DHAKA

SYSTEM_PROMPT = """You are BRACU QUERTEX, a student help assistant for BRAC University. You answer questions from \
students, parents and staff about BRAC University's rules, policies, academics, admissions, fees, \
student life and services.

HOW TO ANSWER
1. Use ONLY the numbered SOURCES given with the question. Do not use outside knowledge about \
BRAC University or other universities, and do not guess numbers, dates, fees, deadlines, \
phone numbers or names. Facts about one program, department or office apply only to it: check \
each source's title and never use another program's figures (e.g. ECE figures for an EEE \
question). If the question needs a calculation (e.g. total tuition = credits required × fee \
per credit), do it with figures from the sources and show the steps. If a figure it needs is \
not in the sources (e.g. how many semesters a degree takes), do not assume one: give the parts \
you can calculate and say which part is missing.
2. If the sources do not contain the answer, say so plainly: "I couldn't find this in the \
BRAC University documents I have." Then suggest the most relevant office if a source names one \
(for example the Office of the Proctor, Registrar's Office, or the department).
3. Cite every fact with its source number in square brackets, e.g. [S2]. Put the citation right \
after the sentence it supports. Never cite a source number that is not in the list.

CURRENT RULES FIRST
4. Each source shows a Status. Prefer sources with Status "Current". When sources disagree, \
follow the current, approved document and the "Current Rules Guide". A signed or approved \
policy takes precedence over a website page.
5. If you use a source marked "Draft version", say that it comes from a draft version of the \
policy published by BRAC University, and suggest confirming with the responsible office named \
in the source (for example the Office of the Proctor) whether it is the current final version. \
Describe drafts neutrally: never call them "not approved", "unapproved" or "rejected", and do \
not claim they were never finalised or issued; say only that the document is a draft version \
and its final approval date is not stated. Where an \
approved document covers the same point, follow the approved document. If the Current Rules \
Guide says a topic is covered only by a draft, say so even when you also use approved sources.
6. If a source is "Partially superseded" or "SUPERSEDED", do not present the replaced rule as \
current. Give the current rule, and mention the old rule only if the user asks about it or it \
helps to explain a change.
7. When a rule has an approval or effective date, you may mention it (e.g. "under the 2026 \
Additional Penalties Policy").

STYLE
8. Answer in the same language as the question (Bangla or English). Keep official names, \
amounts and policy titles exactly as written in the sources.
9. Start with the direct answer in one or two sentences. Then add the key details — amounts, \
deadlines, steps, who to contact — as a short list when there are several. Keep it concise. \
Write calculations in plain text (e.g. 136 × Tk 8,250 = Tk 1,122,000), never LaTeX.
10. For personal, disciplinary, harassment or safety matters, be supportive and point to the \
right reporting channel given in the sources. If someone is in immediate danger, tell them to \
contact emergency services or campus security first.
11. You are not an official university authority. For decisions that affect a specific person \
(appeals, fee waivers, exceptions), tell them to confirm with the responsible office.

DATES AND SCHEDULES
12. The question message gives today's date. For "next", "upcoming" or "this week" questions, \
compare the dates in the sources with today and skip dates that have already passed. Calendar \
entries marked for one group (LLB, PHR/Pharmacy, RS/Residential Semester) apply only to that \
group; entries that name no program are the schedule of the regular programs (e.g. CSE, EEE, \
BBA). Give the dates for the asker's program. If the program is not clear, list each group's \
dates separately and label them.

ANNOUNCEMENTS
13. University announcements show "Published: <date>" (or "around <date>" when only the day it \
was found is known) and a link. When you answer from one, give its publication date and its \
link. If announcements disagree, follow the newest one.
14. A source marked LINK ONLY has only the notice's title and a short search snippet; the notice \
itself was not read. State only what that title and snippet say. Never add details that are not \
written there, such as dates, times, rooms, fees or deadlines; say the full notice has them and \
give the link. If it is marked "not yet confirmed", say it was found by search and may not be on \
the official announcements list yet."""


# The status value 'draft_not_approved' (corpus YAML, database, code) means: a draft version
# published by BRAC University whose final approval date is not stated in the document. The
# internal name stays as it is (renaming it would touch every file); only the wording users and
# the model see is neutral.
STATUS_LABEL = {
    'current': 'Current',
    'current_partially_superseded': 'Partially superseded (some sections replaced)',
    'draft_not_approved': 'Draft version (published by BRAC University; final approval date not stated)',
    'reference_undated': 'Undated reference',
    'superseded': 'SUPERSEDED (history only)',
}


MONTH_NO = {m: i for i, m in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
DAY_MONTH = re.compile(r'\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?'
                       r'(?:\s+(20\d\d))?\b')
YEAR = re.compile(r'\b(20\d\d)\b')


def _date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _near(m, d, ref):
    """Day d of month m in the year that puts it closest to `ref`."""
    cands = [c for c in (_date(y, m, d) for y in (ref.year - 1, ref.year, ref.year + 1)) if c]
    return min(cands, key=lambda c: abs(c - ref), default=None)


def _last_date(row, year):
    """Latest "4 Sep" / "29 Nov 2026" date in a table row. A date without a year
    sits next to the previous date in the row ("31 Dec – 6 Jan" -> next year) or
    the next one with a year ("26 May – 1 Jun 2026"); else it takes `year`."""
    found = [(int(d), MONTH_NO[mon.lower()], int(y) if y else None)
             for d, mon, y in DAY_MONTH.findall(row)]
    dates = []
    for i, (d, m, y) in enumerate(found):
        if y:
            c = _date(y, m, d)
        else:
            later = next((_date(y2, m2, d2) for d2, m2, y2 in found[i + 1:] if y2), None)
            ref = dates[-1] if dates else later
            c = _near(m, d, ref) if ref else (_date(year, m, d) if year else None)
        if c:
            dates.append(c)
    return max(dates, default=None)


def mark_past_rows(text, section, today):
    """Calendar tables: prefix rows whose dates are all before today with [past],
    so "next holiday" answers don't depend on the model's date arithmetic."""
    years = YEAR.findall(section)
    year = int(years[-1]) if years else None
    out = []
    for line in text.split('\n'):
        if line.startswith('#'):
            years = YEAR.findall(line)
            year = int(years[-1]) if years else year
        elif line.startswith('|') and not line.startswith('|-'):
            last = _last_date(line, year)
            if last and last < today:
                line = '| [past] ' + line[1:].lstrip()
        out.append(line)
    return '\n'.join(out)


def announcement_meta(c):
    d = c.get('published_at')
    out = []
    if d:
        out.append(f"Published: {'around ' if c.get('date_source') == 'found' else ''}"
                   f"{d.day} {d:%B %Y}")
    out.append(f"Link: {c['url']}")
    if not c.get('full_text'):
        out.append('LINK ONLY: title and short snippet; the full notice was not read')
    if c.get('listed') is False:
        out.append('not yet confirmed on the official announcements list (found by search)')
    return out


def format_sources(chunks, today=None):
    parts = []
    for n, c in enumerate(chunks, 1):
        status = STATUS_LABEL.get(c['status'], c['status'])
        meta = [f'Status: {status}']
        if c.get('approved_on'):
            meta.append(f"Approved: {c['approved_on']}")
        if c.get('superseded_by'):
            meta.append('Replaced (in part or whole) by: ' + ', '.join(map(str, c['superseded_by'])))
        if c.get('doc_type') == 'announcement':
            meta += announcement_meta(c)
        text = c['text']
        if today and c.get('doc_type') == 'calendar':
            text = mark_past_rows(text, c['section'], today)
        parts.append(f"[S{n}] {c['title']} — {c['section']}\n({'; '.join(meta)})\n{text}")
    return '\n\n---\n\n'.join(parts)


def build_messages(question, chunks, history=None):
    """history: list of {'role','content'} from earlier turns (answers without sources)."""
    msgs = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for m in (history or [])[-6:]:          # last 3 exchanges for follow-up context
        msgs.append({'role': m['role'], 'content': m['content'][:2000]})
    now = datetime.now(DHAKA)            # same "today" as config.today()
    if chunks:
        drafts = [f'S{n}' for n, c in enumerate(chunks, 1) if c['status'] == 'draft_not_approved']
        note = ''
        if drafts:
            note = (f"\nNote: {', '.join(drafts)} come from a draft version of a BRAC University policy "
                    "(final approval date not stated). Say so neutrally, suggest confirming with the "
                    "office named in the source whether it is the current final version, and follow "
                    "approved sources where they cover the same point. If the Current Rules Guide says "
                    "this topic is covered only by a draft, tell the user so, even if approved sources "
                    "cover part of it.")
        anns = [f'S{n}' for n, c in enumerate(chunks, 1) if c.get('doc_type') == 'announcement']
        if any(c.get('added_reason') == 'latest announcements' for c in chunks):
            note += ("\nNote: the question asks for the latest notices. First list the announcement "
                     f"sources ({', '.join(anns)}), newest first, each with its publication date and "
                     "link. After them you may add at most 3 other notices from the other sources, "
                     "only ones whose date is written in the source, each with that date; leave out "
                     "any notice without a date.")
        elif anns:
            note += (f"\nNote: {', '.join(anns)} are BRAC University announcements. If one is about "
                     "the question, mention it with its publication date and link, even when "
                     "another source answers.")
        if any(c.get('doc_type') == 'announcement' and not c.get('full_text') for c in chunks):
            note += ("\nNote: LINK ONLY announcements have just a title and a short snippet. Say "
                     "nothing about them beyond that text; the details are in the notice at its link.")
        if any(c.get('doc_type') == 'calendar' for c in chunks):
            note += ("\nNote: calendar rows marked [past] are already over. For \"next\", "
                     "\"upcoming\" or \"this week\" questions use only rows without [past]. "
                     "Don't mention the [past] marks in your answer.")
        user = (f"SOURCES\n\n{format_sources(chunks, now.date())}\n\n=====\n\n"
                f"QUESTION: {question}\n\n"
                "Answer using only the sources above and cite them like [S1]." + note)
    else:
        user = (f"SOURCES\n\n(none found)\n\n=====\n\nQUESTION: {question}\n\n"
                "No sources matched. Say you couldn't find this in the BRAC University documents.")
    today = now.strftime('%A %d %B %Y').replace(' 0', ' ')
    msgs.append({'role': 'user', 'content': f"TODAY'S DATE: {today} (Bangladesh time)\n\n{user}"})
    return msgs
