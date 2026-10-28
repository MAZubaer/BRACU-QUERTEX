"""Check the chatbot against eval/questions.jsonl (searches the Postgres index
in DATABASE_URL, so run scripts/ingest.py first).

    python eval/evaluate.py                 # retrieval only (no API key needed, free)
    python eval/evaluate.py --plan          # retrieval with the query planner (1 small LLM call each)
    python eval/evaluate.py --answers       # also ask the LLM and check the answers
    python eval/evaluate.py --answers --only q01,q16

Default mode searches the question + the synonym query (free). --plan uses the
same search path as the app: query planner + synonyms, top-k = PLANNED_TOP_K
when the planner made 2+ queries.

Retrieval check: did a document from `expected_docs` appear in the top-k passages?
                 ('ann_*' = any announcement: a trailing * matches a doc_id prefix)
Answer check:    does the answer contain every `must_include` item?
                 ('a|b' means either a or b is enough; case-insensitive)
Writes its own section of eval/report.md (the other mode's section is kept)
and eval/results.jsonl (eval/results_plan.jsonl with --plan).
"""
import argparse, json, re, sys, time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bracu import llm                       # noqa: E402
from bracu.prompts import build_messages    # noqa: E402
from bracu.retriever import Retriever       # noqa: E402
from bracu.rag import plan_search           # noqa: E402


NUMBER_WORDS = {'7': 'seven', '10': 'ten', '14': 'fourteen', '15': 'fifteen', '30': 'thirty'}
# gpt-oss often writes no-break spaces and non-breaking hyphens ("Level 1", "Vice‑Chancellor")
ODD_CHARS = str.maketrans({' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ',
                           '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-'})


def normalize(text):
    return text.translate(ODD_CHARS).lower()


def contains_all(answer, items):
    a = normalize(answer)
    def found(alt):
        alt = normalize(alt.strip())
        return alt in a or (alt in NUMBER_WORDS and NUMBER_WORDS[alt] in a)
    missing = [it for it in items if not any(found(alt) for alt in it.split('|'))]
    return not missing, missing


def expected(doc_id, docs):
    return any(doc_id == e or (e.endswith('*') and doc_id.startswith(e[:-1])) for e in docs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--answers', action='store_true', help='also call the LLM')
    ap.add_argument('--plan', action='store_true', help='use the query planner (costs tokens)')
    ap.add_argument('--k', type=int, default=None,
                    help='passages per question (default: TOP_K, or PLANNED_TOP_K with --plan)')
    ap.add_argument('--only', default='', help='comma-separated question ids')
    ap.add_argument('--sleep', type=float, default=2.5,
                    help='seconds between questions that call an LLM (free tiers allow ~30 per minute)')
    args = ap.parse_args()

    qs = [json.loads(l) for l in open(HERE / 'questions.jsonl', encoding='utf-8') if l.strip()]
    if args.only:
        keep = set(args.only.split(','))
        qs = [q for q in qs if q['id'] in keep]
    r = Retriever()
    mode = ('planner' if args.plan else 'synonyms') + (' + answers' if args.answers else '')
    print(f'Search mode: {r.mode}, queries: {mode}; {len(qs)} questions')

    results = []
    for q in qs:
        # Bangla questions get an English translation added when an LLM is on
        s = plan_search(q['question'], use_planner=args.plan, translate=args.answers or args.plan)
        k = args.k or s['k']
        hits = r.search(s['main'], k=k, extra_queries=s['extra'])
        got = [h['doc_id'] for h in hits]
        rank = next((i + 1 for i, d in enumerate(got) if expected(d, q['expected_docs'])), None)
        res = {'id': q['id'], 'type': q['type'], 'lang': q['lang'], 'question': q['question'],
               'retrieved': got, 'retrieval_hit': rank is not None if q['expected_docs'] else None,
               'rank': rank, 'k': k, 'search': s['search'], 'planner': s['planner']}
        if args.answers:
            info = {}
            try:
                ans = llm.complete(build_messages(q['question'], hits), info)
            except llm.AllProvidersFailed as e:
                ans = f'[ERROR] {e}'
            ok, missing = contains_all(ans, q['must_include'])
            res.update(answer=ans, answer_ok=ok, missing=missing,
                       provider=f"{info.get('provider', '-')}/{info.get('model', '-')}")
        if args.answers or args.plan:
            time.sleep(args.sleep)
        results.append(res)
        flag = {True: 'HIT ', False: 'MISS', None: ' -- '}[res['retrieval_hit']]
        extra = '' if not args.answers else ('  answer OK' if res['answer_ok']
                                              else f"  answer MISSING {res['missing']}")
        print(f"{q['id']} {flag} rank={rank}  {q['question'][:60]}{extra}")

    # ---- summary
    with_docs = [x for x in results if x['retrieval_hit'] is not None]
    ks = '/'.join(map(str, sorted({x['k'] for x in results})))
    planned = sum(1 for x in results if x['planner'])
    head = [f'## Queries: {mode}', '',
            f'Search mode: **{r.mode}**, top-k = {ks}'
            + (f', planner used for {planned}/{len(results)} questions' if args.plan else ''), '']
    lines = []
    def pct(a, b): return f'{a}/{b} ({100 * a / b:.0f}%)' if b else '-'
    hit = sum(x['retrieval_hit'] for x in with_docs)
    mrr = sum(1 / x['rank'] for x in with_docs if x['rank']) / max(len(with_docs), 1)
    lines += [f'- Retrieval hit@{ks}: **{pct(hit, len(with_docs))}**', f'- MRR: {mrr:.2f}']
    for t in sorted({x['type'] for x in with_docs}):
        sub = [x for x in with_docs if x['type'] == t]
        lines.append(f'  - {t}: {pct(sum(x["retrieval_hit"] for x in sub), len(sub))}')
    if args.answers:
        ok = sum(x['answer_ok'] for x in results)
        lines.append(f'- Answers containing the expected facts: **{pct(ok, len(results))}**')
    summary = list(lines)
    lines += ['', '| id | type | retrieval | answer | question | also searched |',
              '|---|---|---|---|---|---|']
    for x in results:
        rr = {True: f"hit (rank {x['rank']})", False: 'MISS', None: 'n/a'}[x['retrieval_hit']]
        aa = '' if not args.answers else ('ok' if x['answer_ok'] else 'missing: ' + ', '.join(x['missing']))
        sq = '<br>'.join(t.replace('\n', ' ').replace('|', '/') for t in x['search'][1:])
        lines.append(f"| {x['id']} | {x['type']} | {rr} | {aa} | {x['question']} | {sq} |")
    write_section(HERE / 'report.md', mode, head + lines)
    name = 'results_plan.jsonl' if args.plan else 'results.jsonl'
    with open(HERE / name, 'w', encoding='utf-8') as f:
        for x in results:
            f.write(json.dumps(x, ensure_ascii=False) + '\n')
    print('\n'.join(summary))
    print(f"Report: {HERE / 'report.md'}")


def write_section(path, mode, lines):
    """Replace this mode's section of the report and keep the others."""
    old = path.read_text(encoding='utf-8') if path.exists() else ''
    sections = dict(re.findall(r'<!-- mode: (.+?) -->\n(.*?)<!-- /mode: \1 -->\n', old, re.S))
    sections[mode] = '\n'.join(lines) + '\n'
    order = sorted(sections, key=lambda m: (m.startswith('planner'), m))   # synonyms first
    body = ''.join(f'<!-- mode: {m} -->\n{sections[m]}<!-- /mode: {m} -->\n\n' for m in order)
    path.write_text('# Evaluation report\n\n' + body, encoding='utf-8')


if __name__ == '__main__':
    main()
