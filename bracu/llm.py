"""LLM calls with automatic fallback through the providers listed in LLM_CHAIN.
 
All supported providers speak the OpenAI chat-completions API, so plain
`requests` is enough. If a provider is rate-limited (429), down (5xx), times out, or the
model was retired (404/400), the next entry in LLM_CHAIN is tried.
 
Provider 'mock' returns an extractive answer without any API (for testing).
"""
import json
 
import requests
 
from . import config
 
PROVIDERS = {   # name: (chat-completions URL, function returning the API key)
    'groq': ('https://api.groq.com/openai/v1/chat/completions', lambda: config.GROQ_API_KEY),
    'gemini': ('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
               lambda: config.GEMINI_API_KEY),
    'openrouter': ('https://openrouter.ai/api/v1/chat/completions', lambda: config.OPENROUTER_API_KEY),
    'github': ('https://models.github.ai/inference/chat/completions', lambda: config.GITHUB_TOKEN),
    'mistral': ('https://api.mistral.ai/v1/chat/completions', lambda: config.MISTRAL_API_KEY),
    'cerebras': ('https://api.cerebras.ai/v1/chat/completions', lambda: config.CEREBRAS_API_KEY),
}
 
 
class AllProvidersFailed(RuntimeError):
    pass
 
 
def parse_chain(chain=None):
    out = []
    for item in (chain or config.LLM_CHAIN).split(','):
        item = item.strip()
        if not item:
            continue
        prov, _, model = item.partition(':')
        out.append((prov.strip().lower(), model.strip()))
    return out
 
 
def available():
    """Which chain entries have an API key."""
    return [(p, m, p == 'mock' or bool(PROVIDERS.get(p, (None, lambda: ''))[1]()))
            for p, m in parse_chain()]


def _payload(model, messages, stream, with_reasoning=True, max_tokens=None):
    max_tokens = max_tokens or config.LLM_MAX_TOKENS
    body = {'model': model, 'messages': messages, 'stream': stream,
            'temperature': config.LLM_TEMPERATURE}
    if 'gpt-oss' in model:
        body['max_completion_tokens'] = max_tokens + 800   # room for reasoning
        if with_reasoning and config.LLM_REASONING_EFFORT:
            body['reasoning_effort'] = config.LLM_REASONING_EFFORT
    else:
        body['max_tokens'] = max_tokens
    return body
 
 
def _mock_stream(messages):
    """Offline stand-in: quotes the first sentences of the top passage."""
    ctx = next((m['content'] for m in messages if m['role'] == 'user'
                and 'SOURCES' in m['content']), '')
    part = ctx.split('[S1]', 1)[-1].split('[S2]', 1)[0].strip()
    body = ' '.join(part.split('\n', 1)[-1].split())[:600]
    text = (f'(Test mode: no LLM was called.) Top source says: {body} [S1]'
            if body else '(Test mode) No sources were found.')
    for w in text.split(' '):
        yield w + ' '
 
 
def stream_chat(messages, info=None, chain=None, max_tokens=None):
    """Yield answer text pieces. `info` (a dict) receives provider/model/errors.
    `chain` (same format as LLM_CHAIN) and `max_tokens` override the defaults."""
    info = info if info is not None else {}
    info['errors'] = []
    for prov, model in parse_chain(chain):
        if prov == 'mock':
            info.update(provider='mock', model='mock')
            yield from _mock_stream(messages)
            return
        if prov not in PROVIDERS:
            info['errors'].append(f'{prov}: unknown provider')
            continue
        url, key_fn = PROVIDERS[prov]
        key = key_fn()
        if not key:
            info['errors'].append(f'{prov}: no API key set')
            continue
        headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
        for attempt in (1, 2):   # second attempt: without reasoning_effort (if 400)
            try:
                r = requests.post(url, headers=headers, stream=True,
                                  timeout=config.LLM_TIMEOUT,
                                  json=_payload(model, messages, True, with_reasoning=attempt == 1,
                                                max_tokens=max_tokens))
            except requests.RequestException as e:
                info['errors'].append(f'{prov}/{model}: {type(e).__name__}')
                break
            if r.status_code == 400 and attempt == 1 and 'gpt-oss' in model:
                continue
            if r.status_code != 200:
                msg = r.text[:200].replace('\n', ' ')
                info['errors'].append(f'{prov}/{model}: HTTP {r.status_code} {msg}')
                break
            info.update(provider=prov, model=model)
            # SSE responses often have no charset, and requests then assumes
            # ISO-8859-1, which garbles Bangla and symbols like "‑" or "【".
            r.encoding = 'utf-8'
            got_any = False
            try:
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        break
                    try:
                        delta = json.loads(data)['choices'][0].get('delta', {})
                    except (ValueError, KeyError, IndexError):
                        continue
                    piece = (delta.get('content') or '').replace('​', '')  # zero-width space
                    if piece:
                        got_any = True
                        yield piece
            except requests.RequestException as e:
                if got_any:
                    yield '\n\n*(The answer was cut off by a connection problem. Please ask again.)*'
                    return
                info['errors'].append(f'{prov}/{model}: stream failed ({type(e).__name__})')
                break
            if got_any:
                return
            info['errors'].append(f'{prov}/{model}: empty answer')
            break
    raise AllProvidersFailed('; '.join(info['errors']) or 'no providers configured')
 
 
def complete(messages, info=None, chain=None, max_tokens=None):
    """Non-streaming convenience wrapper (used by the evaluation script and the planner)."""
    return ''.join(stream_chat(messages, info, chain=chain, max_tokens=max_tokens))
 