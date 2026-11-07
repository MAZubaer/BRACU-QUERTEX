"""Daily Tavily search sync: link-only announcement entries.

    python scripts/sync_search.py --dry-run         # what would be added (searches still cost credits)
    python scripts/sync_search.py                   # up to 5 basic searches = 5 credits
    python scripts/sync_search.py --time-range month --max-searches 2

Hard rules
- Tavily SEARCH endpoint only: no extract, crawl or map, no raw content, no
  answer. This script never sends a request to bracu.ac.bd, not even through
  a third party; it only stores what the search index already shows (title,
  link, snippet).
- A post found on the university site is accepted as it is (listed=true,
  rag_priority=2): no confirmation step. A saved listing page
  (scripts/import_saved_pages.py) can still correct its title and date.
- When a search result has no date, the day it was found is stored and shown
  as "around <date>". A later search that returns the same post with its date
  (or a real snippet instead of none / site-menu text) updates the entry by
  itself; dates from a saved page are never replaced.
- Privacy: files (/sites/default/files, PDFs, documents) are never kept, and
  a snippet that looks like it lists people (student IDs, student emails,
  phone numbers) is dropped; only the title and link are stored.

Needs TAVILY_API_KEY (.env locally, a GitHub secret in Actions) and
DATABASE_URL (use the unpooled URL in Actions).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from bracu import config                                                   # noqa: E402
from announcements_store import (DMY, MDY, WEEKDAY, find_date, normalize_url,  # noqa: E402
                                 publish_date)

QUERIES = ['notice', 'announcement', 'schedule', 'admission', 'registration OR examination']
SEARCH = dict(search_depth='basic', include_domains=['bracu.ac.bd'], max_results=10,
              include_answer=False, include_raw_content=False, include_usage=True)
CUTOFF_DAYS = 60
HOST = 'www.bracu.ac.bd'
NOTE = 'found by search'
# one-segment paths that are site sections, not posts
SECTION_PAGES = {'about', 'academics', 'admissions', 'research', 'students', 'alumni', 'news',
                 'news-archive', 'events', 'event', 'faculty', 'department', 'departments',
                 'node', 'search', 'admin', 'user', 'bidp', 'contact-us', 'sitemap'}
FILE_EXT = re.compile(r'\.(pdf|docx?|xlsx?|pptx?|csv|jpe?g|png|gif|zip|rar)$', re.I)
PERSONAL = [re.compile(r'(?<!\d)\d{8,10}(?!\d)'),                    # student IDs
            re.compile(r'[\w.+-]+@g\.bracu\.ac\.bd', re.I),           # student emails
            re.compile(r'(?<!\d)(?:\+?88[\s-]?)?01[3-9]\d{2}[\s-]?\d{6}(?!\d)')]  # mobile numbers
BOILERPLATE = [re.compile(p, re.I) for p in (
    r'\s*Address:\s*Kha\s*224\b.*$',                    # site footer: "Address: Kha 224 Pragati Sarani, ..."
    r'Contact Us\.?',r'Tel:\s*\+?[\d\s()-]{8,}\.?', r'Email:\s*info@bracu\.ac\.bd\.?',
    r'\.{3}\s*Read more\s*$', r'Read more\s*$')]
DATE_SOURCE_RANK = {'search': 0, 'snippet': 1, 'title': 2, 'found': 3}
MENU_SEP = re.compile(r'\s[·•]\s')     # "Admissions · Apply Now · Undergraduate Admission · ..."


def safe_error(e):
    """Exception text for the (public) Actions log: no hosts, user names, IPs or keys."""
    msg = (str(e).strip().splitlines() or [''])[0]
    msg = re.sub(r'tvly-[\w-]+', 'tvly-***', msg)
    msg = re.sub(r'[\w.-]+\.(?:neon\.tech|aws\.neon\.tech)\b', '<db-host>', msg)
    msg = re.sub(r'\b(user|database|role)\s+"[^"]*"', r'\1 "***"', msg)
    msg = re.sub(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])', '<ip>', msg)
    msg = re.sub(r'postgres(?:ql)?://\S+', '<database-url>', msg)
    return f'{type(e).__name__}: {msg[:200]}'


def check_url(url):
    """(keep, reason) for a canonical URL (normalize_url: no query string or
    fragment; this sync never fetches URLs, so those are just stripped). Keep
    only www.bracu.ac.bd posts at a top-level slug: the announcements listing
    links to exactly those, so a saved listing can match every entry."""
    p = urlsplit(url.strip())
    if p.netloc.lower() != HOST:
        return False, 'other host'
    path = p.path.rstrip('/')
    if path.startswith('/sites/') or FILE_EXT.search(path):
        return False, 'file (privacy guard)'
    seg = [s for s in path.split('/') if s]
    if not seg:
        return False, 'home page'
    if seg[0].lower() in ('search', 'node'):
        return False, 'search or node page'
    if seg[0] == 'news':
        return False, 'news (not an announcement)'
    if len(seg) > 1:
        return False, 'not a top-level post'
    if seg[0].lower() in SECTION_PAGES:
        return False, 'section page'
    if len(re.split(r'[-_]+', seg[0])) < 3:          # posts have 3+ words: fablab-recruitment_circular
        return False, 'not a post slug'
    return True, ''


def clean_title(title, snippet=''):
    """Drop a " - BRAC University" suffix. A title cut short with "..." is
    completed from the snippet when the snippet starts with it (BRACU snippets
    begin with the post title, then ". Publish Date:")."""
    t = re.sub(r'\s*[|-]\s*(?:BRAC University|BRACU)\b.*$', '', (title or '').strip()).strip()
    t = t or (title or '').strip()
    if t.endswith(('...', '…')):
        head = t.rstrip('.… ')
        if head and snippet.startswith(head):
            full = re.split(r'\.?\s+Publish(?:ed)?\s+Date:|\.\s', snippet, maxsplit=1)[0].strip()
            if len(full) > len(head):
                return full
    return t


def has_personal_data(text):
    return any(p.search(text or '') for p in PERSONAL)


def is_menu(text):
    """Site navigation instead of the post (the index sometimes keeps the menu)."""
    return len(MENU_SEP.findall(text or '')) >= 3


def clean_snippet(text):
    t = re.sub(r'\s+', ' ', text or '').strip()
    if is_menu(t):
        return ''
    t = re.sub(r'^' + MDY + r'\s*[—–-]\s*', '', t)          # leading "Sep 30, 2026 — "
    t = re.sub(r'^\d+\s+(?:minute|hour|day|week)s?\s+ago\s*[—–-]\s*', '', t, flags=re.I)  # "2 days ago — "
    for p in BOILERPLATE:
        t = p.sub('', t)
    t = re.sub(r'\s+', ' ', t).strip(' .')
    return (t[:400].rsplit(' ', 1)[0] + ' …') if len(t) > 400 else t


def snippet_date(text):
    """Date in a search snippet: "Publish Date: ...", a leading "Sep 30, 2026 —",
    or "Date: Saturday, October 3, 2026"."""
    t = re.sub(r'\s+', ' ', text or '').strip()
    return (publish_date(t)
            or (find_date(t, (MDY,), r'^') if re.match(MDY + r'\s*[—–-]', t) else None)
            or find_date(t, (MDY, DMY), r'\bDate:\s*' + WEEKDAY))


def search_date(value):
    """Tavily's published_date, if present (ISO "2026-10-27..." or RFC 1123)."""
    if not value:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', value)
    try:
        return date(*map(int, m.groups())) if m else parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def result_date(r, today):
    """(published_at, date_source): Tavily date, else the snippet, else the title
    (not when it is in the future: "to be held on 30th October" is an event
    date), else the day we found it."""
    d = search_date(r.get('published_date'))
    if d:
        return d, 'search'
    d = snippet_date(r.get('content'))
    if d:
        return d, 'snippet'
    d = find_date(r.get('title'))
    if d and d <= today:
        return d, 'title'
    return today, 'found'


def too_old(r, published_at, date_source, today):
    cutoff = today - timedelta(days=CUTOFF_DAYS)
    if date_source != 'found':
        return published_at < cutoff
    # no date at all: a year in the title or URL older than the cutoff year
    years = [int(y) for y in re.findall(r'(?<!\d)(20\d\d)(?!\d)', f"{r.get('title')} {r.get('url')}")]
    return bool(years) and max(years) < cutoff.year


def improvement(stored, published_at, date_source, snippet):
    """The fields a new result improves in a stored link-only search entry:
    a real date instead of "found" (or a better kind of date), a snippet where
    there was none or only site-menu text. Entries from saved pages (inbox,
    full text, date 'page') are never touched. {} = nothing better."""
    if stored.get('source') != 'search' or stored.get('full_text'):
        return {}
    better = {}
    old_rank = DATE_SOURCE_RANK.get(stored.get('date_source'), -1)     # 'page' -> never replaced
    if DATE_SOURCE_RANK[date_source] < old_rank:
        better.update(published_at=published_at, date_source=date_source)
    old_snippet = stored.get('snippet') or ''
    if is_menu(old_snippet) or (snippet and not old_snippet):
        better['snippet'] = snippet
    return better


def select(results, existing, today, log=None):
    """results: Tavily result dicts (all searches). existing: stored URLs, as a
    set or as {url: stored row} (source, date_source, snippet, full_text, title,
    published_at); with rows, a result that improves a stored search entry
    (see improvement()) comes back in the first list with 'improves' set.
    Returns (announcement dicts to save, URLs already stored, Counter of skip
    reasons). `log`, if given, collects ('skip', reason, url) for every skipped
    result and ('canonical', original, url) for every URL whose query string,
    fragment or trailing slash was stripped."""
    best, skipped, seen_again = {}, Counter(), set()
    rows = existing if isinstance(existing, dict) else {}

    def skip(why, url):
        skipped[why] += 1
        if log is not None:
            log.append(('skip', why, url))

    for r in results:
        orig = r.get('url', '').strip()
        url = normalize_url(orig)
        if log is not None and urlsplit(orig)._replace(scheme='https').geturl() != url:
            log.append(('canonical', orig, url))
        ok, why = check_url(url)
        if not ok:
            skip(why, url)
            continue
        published_at, source = result_date(r, today)
        if too_old(r, published_at, source, today):
            skip(f'older than {CUTOFF_DAYS} days', url)
            continue
        private = has_personal_data(r.get('content'))
        snippet = '' if private else clean_snippet(r.get('content'))
        if url in existing:
            seen_again.add(url)
            stored = rows.get(url)
            better = improvement(stored, published_at, source, snippet) if stored else {}
            if not better:
                continue
            ann = dict(url=url, title=stored['title'], published_at=stored['published_at'],
                       date_source=stored['date_source'], source='search', full_text=False,
                       snippet='' if is_menu(stored['snippet']) else (stored['snippet'] or ''),
                       listed=True, note=NOTE, rag_priority=2, snippet_dropped=private)
            ann.update(better)
            ann['improves'] = '+'.join(k for k in ('date_source', 'snippet') if k in better) \
                .replace('date_source', 'date')
            source, snippet = ann['date_source'], ann['snippet']
        else:
            ann = dict(url=url, title=clean_title(r.get('title'), snippet), published_at=published_at,
                       date_source=source, source='search', full_text=False, snippet=snippet,
                       listed=True, note=NOTE, rag_priority=2, snippet_dropped=private)
        old = best.get(url)
        if old is None or (DATE_SOURCE_RANK.get(source, -1), -len(snippet)) < \
                (DATE_SOURCE_RANK.get(old['date_source'], -1), -len(old['snippet'])):
            if old is not None:
                skip('duplicate in this run', url)
            best[url] = ann
        else:
            skip('duplicate in this run', url)
    return list(best.values()), seen_again, skipped


def run_searches(key, queries, time_range):
    from tavily import TavilyClient
    client = TavilyClient(api_key=key)
    results, credits = [], 0
    for q in queries:
        res = client.search(q, time_range=time_range, **SEARCH)
        credits += (res.get('usage') or {}).get('credits', 1)
        results += res.get('results', [])
        print(f'  search {q!r}: {len(res.get("results", []))} results')
    return results, credits


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dry-run', action='store_true', help='print what is new, write nothing')
    ap.add_argument('--max-searches', type=int, default=5, help='searches (credits) this run, max 5')
    ap.add_argument('--time-range', choices=['day', 'week', 'month'], default='week')
    args = ap.parse_args()
    key = os.environ.get('TAVILY_API_KEY')
    if not key:
        sys.exit('TAVILY_API_KEY is not set (.env locally, a GitHub secret in Actions).')
    n = max(0, min(args.max_searches, 5, len(QUERIES)))
    today = config.today()                    # Bangladesh date (the runner's clock is UTC)

    conn = None
    existing = {}
    try:
        from bracu.db import connect
        conn = connect(os.environ.get('DATABASE_URL_UNPOOLED') or config.DATABASE_URL)
        conn.autocommit = True
        cols = ('url', 'title', 'published_at', 'date_source', 'source', 'snippet', 'full_text')
        existing = {r[0]: dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM announcements").fetchall()}
    except Exception as e:
        if not args.dry_run:
            sys.exit(f'Database unavailable: {safe_error(e)}')
        print(f'(dry run without database: {type(e).__name__}; no dedupe against stored posts)')

    print(f'Tavily search: {n} searches, time_range={args.time_range}, depth=basic')
    try:
        results, credits = run_searches(key, QUERIES[:n], args.time_range)
    except Exception as e:                 # quota, bad key, network: fail loudly (Actions emails)
        sys.exit(f'Tavily search failed: {safe_error(e)}')
    log = []
    found, seen_again, skipped = select(results, existing, today, log)
    new = [a for a in found if not a.get('improves')]
    improved = [a for a in found if a.get('improves')]
    for kind, a, b in sorted(log):                     # URLs only, never snippets
        print(f'  canonical {a}\n         -> {b}' if kind == 'canonical' else f'  skip {a:28s} {b}')

    for a in sorted(found, key=lambda a: a['published_at'], reverse=True):
        label = f"BETTER {a['improves']}" if a.get('improves') else 'NEW'
        print(f"  {label}  {a['published_at']} ({a['date_source']:7s}) {a['title'][:70]}\n"
              f"       {a['url']}\n       snippet: {a['snippet'][:110] or '(none)'}")
    saved = Counter()
    if not args.dry_run:
        from announcements_store import Store
        store = Store(conn)
        for a in found:
            saved[store.save(a, a['snippet'] or a['title'])] += 1
        if seen_again:
            conn.execute('UPDATE announcements SET last_seen = now() WHERE url = ANY(%s)',
                         (list(seen_again),))
        # the last successful run, shown by /api/health as "last_search_sync"
        run = {'ran_at': datetime.now(config.DHAKA).isoformat(timespec='minutes'),
               'trigger': os.environ.get('GITHUB_EVENT_NAME', 'local'), 'searches': n,
               'credits': credits, 'results': len(results), 'new': len(new),
               'improved': len(improved), 'already_stored': len(seen_again)}
        conn.execute("""INSERT INTO index_info (key, value) VALUES ('search_sync', %s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps(run),))
    print(f"searches {n} | credits {credits} | results {len(results)} | kept {len(new) + len(seen_again)} "
          f"| new {len(new)}{' (dry run, not saved)' if args.dry_run else ''} | already stored {len(seen_again)}"
          f" (improved {len(improved)})"
          + (f" | saved {dict(saved)}" if saved else '')
          + f" | snippets dropped (personal data) {sum(a['snippet_dropped'] for a in new)}"
          + f" | skipped {sum(skipped.values())}: {dict(skipped)}")
    if conn:
        conn.close()


if __name__ == '__main__':
    main()
