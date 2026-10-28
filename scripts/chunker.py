"""Split the BRACU RAG corpus into retrieval chunks with metadata.

Usage:  python chunker.py <corpus_dir> <out.jsonl>

Rules
- Split on markdown headings (#–####) and keep the heading path of every chunk.
- Merge small neighbouring sections under the same parent until ~TARGET words.
- Split long sections on paragraph boundaries; never split inside a table
  (very long tables are split by rows, repeating the header row).
- Every chunk gets the document's YAML metadata plus its section path.
- `text` is the plain chunk; `context_text` adds a one-line header
  (document, section, status) used for embedding and for the LLM prompt.
"""
import json, os, re, sys, glob
import yaml

TARGET = 350      # words: merge sections until about this size
MAX = 700         # words: split anything longer
MIN = 60          # words: tiny trailing pieces get merged back

HEAD = re.compile(r'^(#{1,4})\s+(.*)$')


def read_doc(path):
    raw = open(path, encoding='utf-8').read()
    meta, body = {}, raw
    if raw.startswith('---\n'):
        end = raw.find('\n---\n', 4)
        meta = yaml.safe_load(raw[4:end]) or {}
        body = raw[end + 5:]
    return meta, body


def clean_heading(h):
    h = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', h)        # links -> text
    return re.sub(r'[*_`]', '', h).strip()


def sections(body):
    """Yield (heading_path, level, text) blocks in document order."""
    path, buf, level = [], [], 0
    in_code = False
    for line in body.split('\n'):
        if line.strip().startswith('```'):
            in_code = not in_code
        m = HEAD.match(line) if not in_code else None
        if m:
            if ''.join(buf).strip():
                yield list(path), level, '\n'.join(buf).strip()
            lvl, title = len(m.group(1)), clean_heading(m.group(2))
            path = [p for p in path if p[0] < lvl] + [(lvl, title)]
            level, buf = lvl, [line]
        else:
            buf.append(line)
    if ''.join(buf).strip():
        yield list(path), level, '\n'.join(buf).strip()


def wc(s):
    return len(s.split())


def split_long(text):
    """Split text > MAX words on paragraph boundaries, keeping tables intact."""
    blocks, cur = [], []
    lines = text.split('\n')
    i = 0
    paras = []
    while i < len(lines):
        if lines[i].startswith('|'):
            tbl = []
            while i < len(lines) and lines[i].startswith('|'):
                tbl.append(lines[i]); i += 1
            paras.append(('table', tbl))
        else:
            p = []
            while i < len(lines) and not lines[i].startswith('|') and lines[i].strip():
                p.append(lines[i]); i += 1
            if p:
                paras.append(('text', p))
            while i < len(lines) and not lines[i].strip():
                i += 1
    out, cur = [], []
    def flush():
        if cur:
            out.append('\n\n'.join(cur)); cur.clear()
    for kind, ls in paras:
        if kind == 'table' and wc('\n'.join(ls)) > MAX and len(ls) > 3:
            header, rows = ls[:2], ls[2:]
            part = []
            for r in rows:
                if wc('\n'.join(header + part + [r])) > MAX and part:
                    flush(); out.append('\n'.join(header + part)); part = []
                part.append(r)
            flush(); out.append('\n'.join(header + part))
            continue
        block = '\n'.join(ls)
        if cur and wc('\n\n'.join(cur + [block])) > MAX:
            flush()
        cur.append(block)
    flush()
    # merge a tiny tail back
    if len(out) > 1 and wc(out[-1]) < MIN:
        tail = out.pop()
        out[-1] = out[-1] + '\n\n' + tail
    return out


def chunk_doc(meta, body):
    # The Current Rules Guide is a lookup sheet: one numbered topic per chunk.
    one_topic_per_chunk = meta.get('doc_id') == 'current_rules_guide'
    secs = list(sections(body))
    pieces = []   # (heading_path, text)
    for path, lvl, text in secs:
        if wc(text) > MAX:
            for part in split_long(text):
                pieces.append((path, part))
        else:
            pieces.append((path, text))
    # merge small neighbours that share the same top-level (H1/H2) parent
    merged = []
    for path, text in pieces:
        if merged:
            ppath, ptext = merged[-1]
            same_parent = [p for p in ppath if p[0] <= 2] == [p for p in path if p[0] <= 2]
            same_doc_top = [p for p in ppath if p[0] <= 1] == [p for p in path if p[0] <= 1]
            fits = wc(ptext) + wc(text) <= MAX
            if one_topic_per_chunk:
                # only a title block merges into the section below it
                fits = fits and wc(ptext) < MIN and ppath == path[:len(ppath)]
            if fits and ((same_parent and wc(ptext) < TARGET)
                         or wc(ptext) < MIN                      # tiny title/cover block
                         or (same_doc_top and wc(text) < MIN)):  # tiny trailing section
                common = [a for a, b in zip(ppath, path) if a == b]
                if wc(ptext) < MIN and not common:
                    common = path
                merged[-1] = (common or ppath, ptext + '\n\n' + text)
                continue
        merged.append((path, text))
    return merged


STATUS_LABEL = {
    'current': 'Current',
    'current_partially_superseded': 'Partially superseded',
    'draft_not_approved': 'DRAFT (not approved)',
    'reference_undated': 'Undated reference',
    'superseded': 'SUPERSEDED (history only)',
}


def main(corpus, out_path):
    files = sorted(glob.glob(os.path.join(corpus, '**', '*.md'), recursive=True))
    n_docs = n_chunks = 0
    with open(out_path, 'w', encoding='utf-8') as fo:
        for f in files:
            rel = os.path.relpath(f, corpus)
            if rel.startswith('_duplicates') or os.path.basename(f) == 'README.md':
                continue
            meta, body = read_doc(f)
            if not meta.get('doc_id'):
                continue
            n_docs += 1
            src = meta.get('source', '')
            url = src if str(src).startswith('http') else ''
            for i, (path, text) in enumerate(chunk_doc(meta, body)):
                section = ' > '.join(t for _, t in path) or meta.get('title', '')
                status = meta.get('status', '')
                header = (f"Document: {meta.get('title','')} | Section: {section} | "
                          f"Status: {STATUS_LABEL.get(status, status)}")
                if meta.get('approved_on'):
                    header += f" | Approved: {meta['approved_on']}"
                rec = {
                    'chunk_id': f"{meta['doc_id']}::{i:03d}",
                    'doc_id': meta['doc_id'],
                    'title': meta.get('title', ''),
                    'section': section,
                    'status': status,
                    'rag_priority': meta.get('rag_priority', 2),
                    'category': meta.get('category', ''),
                    'doc_type': meta.get('doc_type', ''),
                    'source_type': meta.get('source_type', ''),
                    'url': url,
                    'source': src,
                    'approved_on': str(meta.get('approved_on') or ''),
                    'effective_date': str(meta.get('effective_date') or ''),
                    'superseded_by': meta.get('superseded_by') or [],
                    'file': rel,
                    'chunk_index': i,
                    'words': wc(text),
                    'text': text,
                    'context_text': header + '\n\n' + text,
                }
                fo.write(json.dumps(rec, ensure_ascii=False) + '\n')
                n_chunks += 1
    print(f'{n_docs} documents -> {n_chunks} chunks -> {out_path}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
