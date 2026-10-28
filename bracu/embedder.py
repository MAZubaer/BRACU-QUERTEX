"""multilingual-e5-small with onnxruntime (no PyTorch).

Files in model/ (created by scripts/fetch_model.py):
  tokenizer.json
  model_quantized.onnx            or, if split for GitHub's 100 MB limit,
  model_quantized.onnx.part0, .part1, ...

E5 needs "query: " / "passage: " prefixes, mean pooling and L2 normalisation.
Questions and passages must be embedded by THIS class, so ingest.py and the
API produce comparable vectors.
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

    def encode(self, texts, kind='passage', batch_size=16):
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
