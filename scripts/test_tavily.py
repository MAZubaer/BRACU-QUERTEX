"""One-off check of what Tavily's search index knows about bracu.ac.bd posts.

    python scripts/test_tavily.py            # 4 basic searches = 4 credits
    python scripts/test_tavily.py --dry-run  # show the requests, spend nothing

Saves each raw response to tests/fixtures/tavily/<query>.json; the offline
tests use those files, never the live API. Search endpoint only: no extract,
crawl or map, no raw content, no answer (we never fetch BRACU pages, not even
through a third party).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bracu.config  # noqa: E402,F401  (loads .env when python-dotenv is installed)
OUT = ROOT / 'tests' / 'fixtures' / 'tavily'
QUERIES = ['notice', 'schedule Fall 2026', 'admission test', 'registration']
PARAMS = dict(search_depth='basic', include_domains=['bracu.ac.bd'], time_range='month',
              max_results=10, include_answer=False, include_raw_content=False, include_usage=True)


def slug(q):
    return re.sub(r'[^a-z0-9]+', '_', q.lower()).strip('_')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    key =os.environ.get('TAVILY_API_KEY')
    if not key and not args.dry_run:
        sys.exit('TAVILY_API_KEY is not set (.env locally, a GitHub secret in Actions).')

    OUT.mkdir(parents=True, exist_ok=True)
    client = None
    credits = 0
    for q in QUERIES:
        print(f'search {q!r} {PARAMS}')
        if args.dry_run:
            continue
        if client is None:
            from tavily import TavilyClient
            client = TavilyClient(api_key=key)
        res = client.search(q, **PARAMS)
        (OUT / f'{slug(q)}.json').write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                             encoding='utf-8')
        used = (res.get('usage') or {}).get('credits', 1)
        credits += used
        print(f'  {len(res.get("results", []))} results, {used} credit(s)')
    print(f'credits used this run: {credits}')


if __name__ == '__main__':
    main()
