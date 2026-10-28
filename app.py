"""BRACU QUERTEX — FastAPI app.

Local:   uvicorn app:app --reload        then open http://localhost:8000
Vercel:  detected automatically (FastAPI instance named `app` in app.py).

Endpoints
  POST /api/chat       {"question", "history": [...]} -> Server-Sent Events:
                       search {"queries", "planner"}, sources, token (many), done | error
  POST /api/feedback   {"rating": 1|-1, "question", "answer", "source_ids"}
  GET  /api/announcements?limit=5   newest announcements (title, date, date_source, url,
                       full_text, listed); cached 5 minutes
  GET  /api/health     database, index and provider status
"""
import json
import threading
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bracu import config, llm
from bracu.embedder import embedder_status, get_embedder
from bracu.guard import check_rate_limit, client_key, save_feedback
from bracu.rag import ask, cited_numbers, source_card
from bracu.retriever import index_info, latest_announcements

app = FastAPI(title='BRACU QUERTEX API', docs_url='/api/docs', openapi_url='/api/openapi.json')

if config.ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS,
                       allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])

# Load the embedding model in the background as soon as the instance starts,
# so it is usually ready by the time the first question arrives.
if config.USE_EMBEDDINGS:
    threading.Thread(target=get_embedder, daemon=True).start()


# ---------------------------------------------------------------- models
class Message(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: List[Message] = Field(default_factory=list, max_length=40)
    include_history: Optional[bool] = None     # True = also use superseded documents


class FeedbackRequest(BaseModel):
    rating: Literal[1, -1]
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=12000)
    source_ids: List[str] = Field(default_factory=list, max_length=20)
    provider: Optional[str] = Field(default=None, max_length=100)
    comment: Optional[str] = Field(default=None, max_length=1000)


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


# ---------------------------------------------------------------- routes
@app.post('/api/chat')
def chat(req: ChatRequest, request: Request):
    question = req.question.strip()
    if len(question) > config.MAX_QUESTION_CHARS:
        return JSONResponse({'error': f'Please keep questions under {config.MAX_QUESTION_CHARS} '
                                      'characters.'}, status_code=400)
    try:
        limited = check_rate_limit(client_key(request))
    except Exception as e:
        print(f'[chat] rate limit check failed: {e}')
        return JSONResponse({'error': 'The service is starting up. Please try again in a moment.'},
                            status_code=503)
    if limited:
        return JSONResponse({'error': limited}, status_code=429)

    history = [m.model_dump() for m in req.history][-config.MAX_HISTORY_MESSAGES:]

    def events():
        try:
            stream, sources, info = ask(question, history, include_history=req.include_history)
        except Exception as e:
            print(f'[chat] search failed: {type(e).__name__}: {e}')
            yield sse('error', {'message': 'Search is unavailable right now. Please try again.'})
            return
        yield sse('search', {'queries': info.get('search', []), 'planner': info.get('planner')})
        yield sse('sources', [source_card(n, c) for n, c in enumerate(sources, 1)])
        answer = ''
        try:
            for piece in stream:
                answer += piece
                yield sse('token', piece)
        except llm.AllProvidersFailed as e:
            print(f'[chat] all providers failed: {e}')
            yield sse('error', {'message': 'The AI service is busy or over its free limit. '
                                           'Please try again in a minute.'})
            return
        yield sse('done', {'provider': info.get('provider'), 'model': info.get('model'),
                           'fallbacks': len(info.get('errors', [])),
                           'cited': cited_numbers(answer)})

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/feedback')
def feedback(req: FeedbackRequest):
    try:
        save_feedback(req.rating, req.question, req.answer, req.source_ids,
                      req.provider, req.comment)
    except Exception as e:
        print(f'[feedback] {e}')
        return JSONResponse({'ok': False}, status_code=503)
    return {'ok': True}


@app.get('/api/announcements')
def announcements(limit: int = Query(5, ge=1, le=20)):
    """Newest BRAC University announcements for the "Latest notices" panel:
    confirmed (listed) posts first, then provisional ones found by search."""
    try:
        items = latest_announcements(limit)
    except Exception as e:
        print(f'[announcements] {type(e).__name__}: {e}')
        return JSONResponse({'announcements': []}, status_code=503,
                            headers={'Cache-Control': 'no-store'})
    return JSONResponse({'announcements': items},
                        headers={'Cache-Control': 'public, max-age=300, s-maxage=900'})


@app.get('/api/health')
def health():
    out = {'app': 'BRACU QUERTEX', 'ok': True, 'embedder': embedder_status(),
           'providers': [{'provider': p, 'model': m, 'has_key': ok} for p, m, ok in llm.available()]}
    try:
        info = index_info()
        out.update(chunks=info.get('chunks'), index=info.get('build'))
    except Exception as e:
        out.update(ok=False, database=f'unavailable ({type(e).__name__})')
    return out


# The chat page. On Vercel, files in public/ are served by the CDN directly;
# this mount makes the same page work with `uvicorn app:app` on your PC.
PUBLIC = Path(__file__).parent / 'public'
if PUBLIC.exists():
    app.mount('/', StaticFiles(directory=PUBLIC, html=True), name='public')
