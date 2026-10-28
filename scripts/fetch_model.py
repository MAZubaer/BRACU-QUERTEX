"""Download multilingual-e5-small (quantized ONNX) into model/. Run once on your PC.

    python scripts/fetch_model.py

The ONNX file is larger than GitHub's 100 MB per-file limit, so it is saved
in parts of 90 MB (model_quantized.onnx.part0, .part1, ...). The API joins
them in memory, so you can commit model/ to GitHub without Git LFS.
"""
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bracu import config                                    # noqa: E402

REPO = 'https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/'
FILES = {'tokenizer.json': 'tokenizer.json', 'onnx/model_quantized.onnx': 'model_quantized.onnx'}
PART = 90 * 1024 * 1024


def download(url, dest):
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))
        done = 0
        with open(dest, 'wb') as f:
            for block in r.iter_content(1 << 20):
                f.write(block)
                done += len(block)
                if total:
                    print(f'  {dest.name}: {done * 100 // total}%', end='\r', flush=True)
    print(f'  {dest.name}: {dest.stat().st_size / 1e6:.0f} MB      ')


def split(path):
    data = path.read_bytes()
    if len(data) <= PART:
        return
    for old in path.parent.glob(path.name + '.part*'):
        old.unlink()
    for n, start in enumerate(range(0, len(data), PART)):
        (path.parent / f'{path.name}.part{n}').write_bytes(data[start:start + PART])
    path.unlink()
    print(f'  split {path.name} into {n + 1} parts (GitHub-friendly)')


def main():
    out = config.MODEL_DIR
    out.mkdir(parents=True, exist_ok=True)
    for remote, local in FILES.items():
        print(f'Downloading {remote} ...')
        download(REPO + remote, out / local)
    split(out / 'model_quantized.onnx')

    # quick sanity check
    from bracu.embedder import Embedder
    e = Embedder()
    q = e.encode(['What is the fine for smoking on campus?'], kind='query')[0]
    p = e.encode(['Smoking on campus: first offence warning and BDT 5,000 fine.',
                  'The library opens at 8 am on weekdays.'], kind='passage')
    print(f'Similarity  related: {q @ p[0]:.3f}   unrelated: {q @ p[1]:.3f}  '
          f'(related should be clearly higher)')


if __name__ == '__main__':
    main()
