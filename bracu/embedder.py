"""multilingual-e5-small with onnxruntime (no PyTorch).

Files in model/ (created by scripts/fetch_model.py):
  tokenizer.json
  model_quantized.onnx            or, if split for GitHub's 100 MB limit,
  model_quantized.onnx.part0, .part1, ...

E5 needs "query: " / "passage: " prefixes, mean pooling and L2 normalisation.
Questions and passages must be embedded by THIS class, so ingest.py and the
API produce comparable vectors.

The model reads at most 512 tokens. A longer passage (tables full of course codes
often are) is cut into windows that each fit, with its "Document: ... | Section:
..." header repeated, and the window vectors are averaged. Without this, the end
of a long passage was never embedded (e.g. the CST row of a degree plan's GenEd
table). A passage that fits gets exactly the same vector as before. Questions are
short and are embedded in one piece.
"""
import threading
from pathlib import Path

import numpy as np

from . import config

MODEL_NAME = 'model_quantized.onnx'


def _model_bytes_or_path(model_dir: Path):
    single = model_dir / MODEL_NAME
    if single.exists():
        return str(single)
    parts = sorted(model_dir.glob(MODEL_NAME + '.part*'),
                   key=lambda p: int(p.suffix.replace('.part', '')))
    if not parts:
        raise FileNotFoundError(
            f'No ONNX model in {model_dir}. Run: python scripts/fetch_model.py')
    return b''.join(p.read_bytes() for p in parts)


class Embedder:
    def __init__(self, model_dir=None, max_length=512, threads=None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_dir = Path(model_dir or config.MODEL_DIR)
        self.tok = Tokenizer.from_file(str(model_dir / 'tokenizer.json'))
        self.tok.enable_truncation(max_length=max_length)
        self._counter = Tokenizer.from_file(str(model_dir / 'tokenizer.json'))  # no truncation
        self.max_length = max_length
        pad_id = self.tok.token_to_id('<pad>')
        self.tok.enable_padding(pad_id=1 if pad_id is None else pad_id, pad_token='<pad>')

        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            _model_bytes_or_path(model_dir), sess_options=opts,
            providers=['CPUExecutionProvider'])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.dim = config.EMBED_DIM
        self.name = config.EMBED_MODEL

    def _tokens(self, text):
        return len(self._counter.encode(text, add_special_tokens=False).ids)

    def windows(self, text, kind='passage'):
        """`text` as pieces that each fit the model's window (whole lines; a very long
        line is split between words). The "Document: ..." header, if any, starts
        every piece. A text that fits is returned as it is, in one piece."""
        limit = self.max_length - 16                    # room for special tokens
        prefix = f'{kind}: '
        if self._tokens(prefix + text) <= limit:
            return [text]
        header, body = '', text
        if text.startswith('Document:') and '\n\n' in text:
            header, body = text.split('\n\n', 1)
            header += '\n\n'
        room = max(limit - self._tokens(prefix + header), 64)

        def units():
            for line in body.split('\n'):
                n = self._tokens(line)
                if n <= room:
                    yield line, n
                    continue
                part, part_n = [], 0
                for word in line.split(' '):
                    wn = self._tokens(word) + 1
                    if part and part_n + wn > room:
                        yield ' '.join(part), part_n
                        part, part_n = [], 0
                    part.append(word)
                    part_n += wn
                if part:
                    yield ' '.join(part), part_n

        pieces, cur, cur_n = [], [], 0
        for line, n in units():
            if cur and cur_n + n + 1 > room:
                pieces.append('\n'.join(cur))
                cur, cur_n = [], 0
            cur.append(line)
            cur_n += n + 1
        if cur:
            pieces.append('\n'.join(cur))
        pieces = [header + p for p in pieces if p.strip()]
        return pieces or [text]

    def encode(self, texts, kind='passage', batch_size=16):
        """One L2-normalised vector per text. Passages longer than the model's window:
        the mean of their windows' vectors (see windows()), normalised again."""
        if kind != 'passage':
            return self._encode(list(texts), kind, batch_size)
        owner, flat = [], []
        for i, t in enumerate(texts):
            for w in self.windows(t, kind):
                owner.append(i)
                flat.append(w)
        vecs = self._encode(flat, kind, batch_size)
        if len(flat) == len(texts):                     # every text fitted: as before
            return vecs
        out = np.zeros((len(texts), vecs.shape[1]), np.float32)
        np.add.at(out, owner, vecs)
        counts = np.bincount(owner, minlength=len(texts))
        multi = counts > 1
        out[multi] /= np.linalg.norm(out[multi], axis=1, keepdims=True).clip(1e-12)
        return out

    def _encode(self, texts, kind, batch_size):
        out = []
        for start in range(0, len(texts), batch_size):
            batch = [f'{kind}: {t}' for t in texts[start:start + batch_size]]
            enc = self.tok.encode_batch(batch)
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {'input_ids': ids, 'attention_mask': mask}
            if 'token_type_ids' in self.inputs:
                feed['token_type_ids'] = np.zeros_like(ids)
            hidden = self.session.run(None, feed)[0]            # (batch, seq, 384)
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            pooled /= np.linalg.norm(pooled, axis=1, keepdims=True).clip(1e-12)
            out.append(pooled.astype(np.float32))
        return np.vstack(out) if out else np.zeros((0, self.dim), np.float32)


_embedder = None
_lock = threading.Lock()
_error = None


def get_embedder():
    """Loaded once per server instance. Returns None (keyword-only search)
    if embeddings are switched off or the model files are missing."""
    global _embedder, _error
    if not config.USE_EMBEDDINGS or _error:
        return None
    if _embedder is None:
        with _lock:
            if _embedder is None and not _error:
                try:
                    _embedder = Embedder()
                except Exception as e:          # missing files / packages
                    _error = f'{type(e).__name__}: {e}'
                    print(f'[embedder] vector search off ({_error}); using keyword search only')
    return _embedder


def embedder_status():
    if not config.USE_EMBEDDINGS:
        return 'off (USE_EMBEDDINGS=false)'
    return 'error: ' + _error if _error else ('loaded' if _embedder else 'not loaded yet')


def to_pgvector(vec):
    """numpy vector -> pgvector text literal, e.g. '[0.1,0.2]'."""
    return '[' + ','.join(f'{x:.7g}' for x in vec) + ']'
