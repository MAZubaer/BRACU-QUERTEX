"""Import BRAC University pages you saved in your browser (Ctrl+S, "Webpage, HTML only").

    python scripts/import_saved_pages.py --dry-run     # show what would change, touch nothing
    python scripts/import_saved_pages.py               # import data/inbox/*.html
    python scripts/import_saved_pages.py --dry-run tests/fixtures/*.html

Two kinds of page (recognised by their canonical / og:url):
- The announcements LISTING (https://www.bracu.ac.bd/news-archive/announcements,
  any page): the authoritative list. Every post on it is created or confirmed
  as a link-only entry with its exact title and "Publish Date" (listed=true,
  rag_priority=2). Provisional search entries in the page's date range that
  are NOT on it are deleted (reconciliation). Full-text imports are never deleted.
- A single POST page (a top-level slug): its main text is imported (full_text),
  replacing any link-only entry for that URL. PDF links are recorded, never
  downloaded.

Anything else (a Cloudflare "Just a moment..." page, another site, a page
without a canonical URL) goes to data/inbox/failed/ with a .txt reason.
Processed files go to data/inbox/done/. No request is sent to bracu.ac.bd.
"""
import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]

from bs4 import BeautifulSoup, Comment, NavigableString                    # noqa: E402

from bracu import config                                                  # noqa: E402
from announcements_store import (MDY, find_date, normalize_url,           # noqa: E402
                                 publish_date)
from sync_search import PERSONAL, check_url                               # noqa: E402

INBOX = ROOT / 'data' / 'inbox'
SITE = 'https://www.bracu.ac.bd/'
LISTING_PATH = '/news-archive/announcements'
POST_ROWS = 'div.view-news-archive div.view-content div.views-row article.node-announcement'
CONTENT = ['.pane-node-body .field-item', '.field-name-body .field-item', 'article .field-item',
           'article .content', 'main']
TITLE = ['.pane-node-title h1', 'h1.page-title', 'h1.page-h1', 'h1']
FILE_LINK = re.compile(r'\.(pdf|docx?|xlsx?|pptx?)$|/sites/default/files/', re.I)
PERSONAL_LIMIT = 5        # more matches than this: a list of people, keep the post link-only
NOTE_LISTING = 'link only — from the announcements listing'
NOTE_CONFIRMED = 'link only — confirmed on the announcements listing'


class NotImportable(Exception):
    """The file is not a page we import; the message says why."""


# ---------------------------------------------------------------- reading
def load(path):
    return BeautifulSoup(Path(path).read_text(encoding='utf-8', errors='replace'), 'lxml')


def is_challenge(soup):
    title = (soup.title.get_text(strip=True) if soup.title else '').lower()
    html = str(soup)[:20000]
    return (title in ('just a moment...', 'attention required! | cloudflare')
            or 'cf_chl_opt' in html or '/cdn-cgi/challenge-platform/' in html)


def canonical_url(soup):
    for sel, attr in (('link[rel=canonical]', 'href'), ('meta[property="og:url"]', 'content')):
        el = soup.select_one(sel)
        if el and el.get(attr):
            return normalize_url(urljoin(SITE, el[attr]))
    return None


def decode_cf_email(hexstr):
    """Cloudflare email obfuscation: first byte is the XOR key."""
    key = int(hexstr[:2], 16)
    return ''.join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def decode_emails(soup):
    for el in soup.select('[data-cfemail]'):
        el.replace_with(decode_cf_email(el['data-cfemail']))
    for a in soup.select('a[href*="/cdn-cgi/l/email-protection#"]'):
        email = decode_cf_email(a['href'].split('#', 1)[1])
        a['href'] = f'mailto:{email}'
        if not a.get_text(strip=True):
            a.string = email


def classify(soup):
    """('listing' | 'post', canonical URL) or raise NotImportable."""
    if is_challenge(soup):
        raise NotImportable('Cloudflare challenge page ("Just a moment..."), not the notice. Open the '
                            'page in your browser, wait until it has loaded, then save it again.')
    url = canonical_url(soup)
    if not url:
        raise NotImportable('no canonical / og:url: save the page with "Webpage, HTML only".')
    p = urlsplit(url)
    if p.path == LISTING_PATH:
        if not soup.select(POST_ROWS):
            raise NotImportable('announcements listing without any posts')
        return 'listing', url
    ok, why = check_url(url)
    if not ok:
        raise NotImportable(f'not an announcement post ({why}): {url}')
    return 'post', url


# ---------------------------------------------------------------- listing
def listing_link_problem(url):
    """The listing is authoritative, so only the hard rules apply to its links (not
    the search heuristics): www.bracu.ac.bd, one path segment, not a file."""
    p = urlsplit(url)
    seg = [s for s in p.path.split('/') if s]
    if p.netloc != 'www.bracu.ac.bd':
        return 'other host'
    if p.path.startswith('/sites/') or FILE_LINK.search(p.path):
        return 'file (privacy guard)'
    if len(seg) != 1:
        return 'not a top-level post'
    return None


def parse_listing(soup):
    """{'page', 'has_next', 'posts': [{url, title, published_at}]} from a saved
    announcements listing. Only the post rows are read (no menu, filter,
    pager or footer links)."""
    posts = []
    for art in soup.select(POST_ROWS):
        a = art.select_one('h2 a[href]')
        d = art.select_one('span.date-display-single')
        if not a or not d:
            continue
        url = normalize_url(urljoin(SITE, a['href']))
        why = listing_link_problem(url)
        if why:                          # privacy guard: no files; only www top-level posts
            print(f'  (skipped listing link: {why}: {url})')
            continue
        published = find_date(d.get_text(' ', strip=True), (MDY,))
        if not published:
            raise NotImportable(f'unreadable Publish Date {d.get_text(strip=True)!r} for {url}')
        posts.append({'url': url, 'title': ' '.join(a.get_text(' ', strip=True).split()),
                      'published_at': published})
    pager = soup.select_one('ul.pager')
    cur = pager.select_one('li.current') if pager else None
    page = int(cur.get_text(strip=True)) if cur and cur.get_text(strip=True).isdigit() else 1
    has_next = bool(pager and any('next' in a.get_text(strip=True).lower()
                                  or (a.get('title') or '').lower() == 'go to next page'
                                  for a in pager.select('li a[href]') if a['href']))
    return {'page': page, 'has_next': has_next, 'posts': posts}


def reconcile_range(listing):
    """(oldest, newest, oldest_included): posts on the oldest date may continue
    on the next page, so that date only counts on the last page."""
    dates = [p['published_at'] for p in listing['posts']]
    return min(dates), max(dates), not listing['has_next']


def plan_listing(listing, existing, saved_at):
    """What importing a listing does. existing: url -> {source, full_text, listed,
    published_at, first_seen, snippet}. saved_at: when the file was saved (provisional
    entries first seen after that are never deleted: the listing could not show them).
    Returns a list of actions (kind, url, details)."""
    actions, on_page = [], set()
    for p in listing['posts']:
        on_page.add(p['url'])
        row = existing.get(p['url'])
        if row and row['full_text']:
            actions.append(('keep', p['url'], {**p, 'why': 'full text already imported; marked listed'}))
        elif row:
            actions.append(('confirm', p['url'], {**p, 'row': row}))
        else:
            actions.append(('add', p['url'], p))
    lo, hi, lo_included = reconcile_range(listing)
    for url, row in existing.items():
        d = row['published_at']
        if (url in on_page or row['source'] != 'search' or row['listed'] or row['full_text']
                or d is None or not (lo < d <= hi or (lo_included and d == lo))):
            continue
        if row.get('first_seen') and saved_at and row['first_seen'] > saved_at:
            actions.append(('spare', url, {'why': 'found by search after this listing was saved'}))
            continue
        actions.append(('delete', url, {'published_at': d, 'title': row.get('title')}))
    return actions


def listing_ann(p, row=None):
    """The announcements entry for a post on the listing (new, or a confirmed search row)."""
    return dict(url=p['url'], title=p['title'], published_at=p['published_at'], date_source='page',
                source=row['source'] if row else 'inbox', full_text=False,
                snippet=row.get('snippet') if row else None, listed=True,
                note=NOTE_CONFIRMED if row else NOTE_LISTING, rag_priority=2)


# ---------------------------------------------------------------- post page
def table_text(table):
    rows = []
    for tr in table.find_all('tr'):
        cells = [' '.join(c.get_text(' ', strip=True).split()) for c in tr.find_all(['th', 'td'])]
        if any(cells):
            rows.append('| ' + ' | '.join(cells) + ' |')
    if len(rows) > 1:
        rows.insert(1, '|' + '---|' * rows[0].count(' | ') + '---|')
    return '\n'.join(rows)


BLOCKS = {'p', 'div', 'section', 'article', 'ul', 'ol', 'blockquote', 'pre', 'hr', 'dl', 'dd', 'dt'}


def to_text(el):
    """Main text as light markdown: headings, list items, tables one line per row."""
    out = []

    def walk(n):
        if isinstance(n, Comment):
            return
        if isinstance(n, NavigableString):
            out.append(str(n))
            return
        name = n.name
        if name in ('script', 'style', 'noscript', 'form', 'button', 'img', 'iframe'):
            return
        if name == 'table':
            out.append('\n\n' + table_text(n) + '\n\n')
            return
        if name == 'br':
            out.append('\n')
            return
        if name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            out.append('\n\n' + '#' * min(int(name[1]) + 1, 4) + ' ')
            for c in n.children:
                walk(c)
            out.append('\n\n')
            return
        if name == 'li':
            out.append('\n- ')
        elif name in BLOCKS:
            out.append('\n\n')
        for c in n.children:
            walk(c)
        if name in BLOCKS or name == 'li':
            out.append('\n')

    walk(el)
    lines = [re.sub(r'[ \t ]+', ' ', ln).strip() for ln in ''.join(out).split('\n')]
    text = '\n'.join(lines)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def page_date(soup, body, title, today):
    """(published_at, date_source): article:published_time, the post's own "Publish Date"
    (in its Drupal node panes or article, not a sidebar of other posts), a date in
    the title (not a future event date), else today ('found')."""
    meta = soup.select_one('meta[property="article:published_time"]') \
        or soup.select_one('meta[property="article:modified_time"]')
    if meta and meta.get('content'):
        try:
            dt = datetime.fromisoformat(meta['content'].replace('Z', '+00:00'))
            return (dt.astimezone(config.DHAKA).date() if dt.tzinfo else dt.date()), 'page'
        except ValueError:
            pass
    own = soup.select('[class*="pane-node"]') or [body.find_parent('article') or body]
    d = publish_date(' '.join(el.get_text(' ', strip=True) for el in own))
    if d:
        return d, 'page'
    d = find_date(title)
    if d and d <= today:
        return d, 'title'
    return today, 'found'


def parse_post(soup, url, today):
    """{url, title, published_at, date_source, text, pdf_links, personal} from a saved post page."""
    decode_emails(soup)
    title_el = next((soup.select_one(s) for s in TITLE if soup.select_one(s)), None)
    og = soup.select_one('meta[property="og:title"]')
    title = ' '.join((title_el.get_text(' ', strip=True) if title_el else
                      (og['content'] if og else '')).split())
    title = re.sub(r'\s*\|\s*BRAC University\s*$', '', title)
    body = next((soup.select_one(s) for s in CONTENT if soup.select_one(s)), None)
    text = to_text(body) if body else ''
    if not title or not text:
        raise NotImportable(f'no {"title" if not title else "main content"} found in the post page')
    pdfs, seen = [], set()
    for a in body.select('a[href]'):
        href = urljoin(url, a['href'])
        if FILE_LINK.search(urlsplit(href).path) and href not in seen:
            seen.add(href)
            item = {'title': ' '.join(a.get_text(' ', strip=True).split()) or href.rsplit('/', 1)[-1],
                    'url': href}
            if '/sites/default/files/registrar' in href:
                item['note'] = 'link only — registrar file'
            pdfs.append(item)
    published_at, source = page_date(soup, body, title, today)
    personal = sum(len(p.findall(text)) for p in PERSONAL)
    return {'url': url, 'title': title, 'published_at': published_at, 'date_source': source,
            'text': text, 'pdf_links': pdfs, 'personal': personal}


def post_ann(post):
    """(announcement dict, text to index) for a parsed post page. Privacy guard:
    a few personal details are removed; a page that lists people (more than
    PERSONAL_LIMIT matches) is kept link-only."""
    text, note = post['text'], None
    if post['personal'] > PERSONAL_LIMIT:
        text, note = '', 'link only — full text not stored: it lists personal data'
    elif post['personal']:
        for p in PERSONAL:
            text = p.sub('[removed]', text)
        note = 'personal details removed'
    full = bool(text)
    return dict(url=post['url'], title=post['title'], published_at=post['published_at'],
                date_source=post['date_source'], source='inbox', full_text=full, snippet=None,
                listed=False, note=note, rag_priority=2, pdf_links=post['pdf_links']), text


# ---------------------------------------------------------------- running
def existing_rows(conn):
    cols = ['url', 'source', 'full_text', 'listed', 'published_at', 'first_seen', 'snippet', 'title']
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM announcements").fetchall()
    return {r[0]: dict(zip(cols, r)) for r in rows}


def move(path, folder, reason=None):
    """Move a processed inbox file to done/ or failed/ (with a .txt reason).
    Files given from elsewhere (e.g. tests/fixtures) are never moved."""
    if path.resolve().parent != INBOX.resolve():
        if reason:
            print(f'        (not moved: {path} is outside {INBOX})')
        return None
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = folder / f'{stamp}_{path.name}'
    shutil.move(str(path), dest)
    assets = path.with_name(path.stem + '_files')         # "Webpage, Complete" saves a folder too
    if assets.is_dir():
        shutil.move(str(assets), folder / f'{stamp}_{assets.name}')
    if reason:
        dest.with_suffix(dest.suffix + '.txt').write_text(reason + '\n', encoding='utf-8')
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('files', nargs='*', help='saved pages (default: data/inbox/*.html, *.htm)')
    ap.add_argument('--dry-run', action='store_true', help='show what would change; write and move nothing')
    args = ap.parse_args()
    files = [Path(f) for f in args.files] or sorted(
        p for p in INBOX.glob('*') if p.suffix.lower() in ('.html', '.htm'))
    if not files:
        print(f'Nothing to import in {INBOX}')
        return
    today = config.today()

    from bracu.db import connect
    conn = connect(os.environ.get('DATABASE_URL_UNPOOLED') or config.DATABASE_URL)
    conn.autocommit = True
    store = None
    if not args.dry_run:
        from announcements_store import Store
        store = Store(conn)

    parsed = []                                  # posts before listings, so a listing sees them
    for f in files:
        try:
            soup = load(f)
            kind, url = classify(soup)
            parsed.append((0 if kind == 'post' else 1, f, kind, soup, url))
        except NotImportable as e:
            print(f'FAILED  {f.name}: {e}')
            if not args.dry_run:
                move(f, INBOX / 'failed', str(e))
    totals = {}
    for _, f, kind, soup, url in sorted(parsed, key=lambda x: x[0]):
        try:
            if kind == 'post':
                post = parse_post(soup, url, today)
                ann, text = post_ann(post)
                print(f"POST    {f.name}\n        {post['published_at']} ({post['date_source']}) "
                      f"{post['title']}\n        {url}\n        {len(text.split())} words, "
                      f"{len(post['pdf_links'])} file links{', ' + ann['note'] if ann['note'] else ''}")
                result = 'would import' if args.dry_run else store.save(ann, text)
                totals[f'post {result}'] = totals.get(f'post {result}', 0) + 1
            else:
                listing = parse_listing(soup)
                saved_at = datetime.fromtimestamp(f.stat().st_mtime).astimezone()
                actions = plan_listing(listing, existing_rows(conn), saved_at)
                lo, hi, inc = reconcile_range(listing)
                print(f"LISTING {f.name}: page {listing['page']}"
                      f"{'' if listing['has_next'] else ' (last page)'}, {len(listing['posts'])} posts, "
                      f"reconcile {'from' if inc else 'after'} {lo} up to {hi} (inclusive)")
                for kind_, u, d in actions:
                    when = d.get('published_at', '')
                    title = d.get('title') or ''
                    extra = ''
                    if kind_ == 'confirm':
                        r = d['row']
                        extra = (f"  [{r['source']}, date {r['published_at']}->{d['published_at']} "
                                 f"page, listed false->true, priority ->2]")
                    elif kind_ in ('keep', 'spare'):
                        extra = f"  [{d['why']}]"
                    print(f'  {kind_.upper():8s}{when!s:11s} {title[:70]}{extra}\n          {u}')
                    totals[kind_] = totals.get(kind_, 0) + 1
                    if args.dry_run:
                        continue
                    if kind_ in ('add', 'confirm', 'keep'):
                        store.save(listing_ann(d, d.get('row')), (d.get('row') or {}).get('snippet') or '')
                    elif kind_ == 'delete':
                        store.delete(u)
            if not args.dry_run:
                move(f, INBOX / 'done')
        except NotImportable as e:
            print(f'FAILED  {f.name}: {e}')
            if not args.dry_run:
                move(f, INBOX / 'failed', str(e))
    print(('DRY RUN (nothing written or moved): ' if args.dry_run else 'done: ') + str(totals))
    conn.close()


if __name__ == '__main__':
    main()
