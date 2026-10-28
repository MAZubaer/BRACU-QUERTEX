"""All settings in one place. Values come from environment variables
(.env on your PC, Project Settings -> Environment Variables on Vercel)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:                                    # .env is only used locally
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass


def _get(name, default=''):
    return os.environ.get(name, default).strip()


def _bool(name, default):
    return _get(name, default).lower() in ('1', 'true', 'yes')


# ---- database (Neon Postgres + pgvector) -------------------------------
# Use Neon's *pooled* connection string (host contains "-pooler").
# The Vercel-Neon integration sets DATABASE_URL automatically.
DATABASE_URL = _get('DATABASE_URL') or _get('POSTGRES_URL')

# ---- data (used by scripts/ingest.py on your PC) -----------------------
CORPUS_DIR = Path(_get('CORPUS_DIR', str(ROOT / 'BRACU_RAG_Corpus')))
CHUNKS_FILE = Path(_get('CHUNKS_FILE', str(ROOT / 'data' / 'chunks.jsonl')))

# ---- embeddings: multilingual-e5-small as quantized ONNX ---------------
# No PyTorch: onnxruntime + tokenizers keep the Vercel bundle small.
# scripts/fetch_model.py downloads the files into model/.
MODEL_DIR = Path(_get('MODEL_DIR', str(ROOT / 'model')))
EMBED_MODEL = _get('EMBED_MODEL', 'Xenova/multilingual-e5-small')
EMBED_DIM = 384
USE_EMBEDDINGS = _bool('USE_EMBEDDINGS', 'true')

# ---- retrieval ---------------------------------------------------------
TOP_K = int(_get('TOP_K', '5'))                  # passages sent to the LLM
MAX_PER_DOC = int(_get('MAX_PER_DOC', '3'))      # diversity: max passages per document
MAX_CONTEXT_WORDS = int(_get('MAX_CONTEXT_WORDS', '2500'))
CANDIDATES = int(_get('CANDIDATES', '60'))       # per search method, before fusion
TRANSLATE_QUERIES = _bool('TRANSLATE_QUERIES', 'true')   # Bangla -> add English for search
LATEST_ANNOUNCEMENTS = int(_get('LATEST_ANNOUNCEMENTS', '8'))  # added for "latest notices" questions

DHAKA = timezone(timedelta(hours=6))             # Bangladesh time (no daylight saving)


def today():
    """Today's date in Bangladesh (dates in the prompt, announcement ages)."""
    return datetime.now(DHAKA).date()

# ---- query planner: one fast LLM call rewrites the question into document-style queries
QUERY_PLANNER = _bool('QUERY_PLANNER', 'true')
PLANNER_CHAIN = _get(
    'PLANNER_CHAIN',
    'groq:openai/gpt-oss-20b,gemini:gemini-3.5-flash-lite,openrouter:openai/gpt-oss-20b')
MAX_PLANNED_QUERIES = int(_get('MAX_PLANNED_QUERIES', '4'))
PLANNED_TOP_K = int(_get('PLANNED_TOP_K', '7'))  # passages when the planner made 2+ queries

# ---- LLM providers -----------------------------------------------------
GROQ_API_KEY = _get('GROQ_API_KEY')              # console.groq.com
GEMINI_API_KEY = _get('GEMINI_API_KEY')          # aistudio.google.com
OPENROUTER_API_KEY = _get('OPENROUTER_API_KEY')
GITHUB_TOKEN = _get('GITHUB_TOKEN')
MISTRAL_API_KEY = _get('MISTRAL_API_KEY')
CEREBRAS_API_KEY = _get('CEREBRAS_API_KEY')

# provider:model, tried left to right; entries without a key are skipped.
LLM_CHAIN = _get(
    'LLM_CHAIN',
    'groq:openai/gpt-oss-120b,groq:openai/gpt-oss-20b,gemini:gemini-3.8-flash,'
    'gemini:gemini-3.5-flash-lite,openrouter:openai/gpt-oss-120b')
LLM_TEMPERATURE = float(_get('LLM_TEMPERATURE', '0.1'))
LLM_MAX_TOKENS = int(_get('LLM_MAX_TOKENS', '1200'))
LLM_REASONING_EFFORT = _get('LLM_REASONING_EFFORT', 'low')
LLM_TIMEOUT = float(_get('LLM_TIMEOUT', '60'))

# ---- public API guardrails ---------------------------------------------
MAX_QUESTION_CHARS = int(_get('MAX_QUESTION_CHARS', '500'))
MAX_HISTORY_MESSAGES = int(_get('MAX_HISTORY_MESSAGES', '6'))
RATE_LIMIT_10MIN = int(_get('RATE_LIMIT_10MIN', '10'))   # questions per IP per 10 minutes
RATE_LIMIT_DAY = int(_get('RATE_LIMIT_DAY', '40'))       # questions per IP per day (0 = off)
# Extra sites allowed to call the API from a browser (comma-separated).
# Not needed when the page is served by this same app.
ALLOWED_ORIGINS = [o.strip() for o in _get('ALLOWED_ORIGINS').split(',') if o.strip()]
# Salt for hashing IPs (rate limits store only a hash, never the IP).
IP_HASH_SALT = _get('IP_HASH_SALT', 'change-me')
