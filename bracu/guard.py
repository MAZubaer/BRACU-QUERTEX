"""Rate limiting and feedback, stored in the same Neon database."""
import hashlib
import random
import time
from datetime import datetime, timezone

from . import config, db


def client_key(request):
    """Hashed client IP (the IP itself is never stored). On Vercel the first
    X-Forwarded-For entry is the real client address."""
    fwd = request.headers.get('x-forwarded-for', '')
    ip = (fwd.split(',')[0].strip() or request.headers.get('x-real-ip', '')
          or (request.client.host if request.client else 'unknown'))
    return hashlib.sha256(f'{config.IP_HASH_SALT}:{ip}'.encode()).hexdigest()[:32]


def check_rate_limit(key):
    """Count this question. Returns None if allowed, else a message for the user."""
    limits = []
    now = time.time()
    if config.RATE_LIMIT_10MIN > 0:
        limits.append((f'10m:{int(now // 600)}', config.RATE_LIMIT_10MIN,
                       'You are asking very quickly. Please wait a few minutes and try again.'))
    if config.RATE_LIMIT_DAY > 0:
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        limits.append((f'day:{day}', config.RATE_LIMIT_DAY,
                       "You've reached today's question limit. Please come back tomorrow."))
    if not limits:
        return None

    def q(cur):
        values = ', '.join(['(%s, %s, 1)'] * len(limits))
        params = [x for b, _, _ in limits for x in (key, b)]
        cur.execute(f"""
            INSERT INTO rate_limits (key, bucket, count) VALUES {values}
            ON CONFLICT (key, bucket)
            DO UPDATE SET count = rate_limits.count + 1, updated_at = now()
            RETURNING bucket, count""", params)
        counts = dict(cur.fetchall())
        if random.random() < 0.02:          # tidy old windows now and then
            cur.execute("DELETE FROM rate_limits WHERE updated_at < now() - interval '2 days'")
        return counts

    counts = db.run(q)
    for bucket, limit, msg in limits:
        if counts.get(bucket, 0) > limit:
            return msg
    return None


def save_feedback(rating, question, answer, source_ids, provider=None, comment=None):
    db.fetchall("""
        INSERT INTO feedback (rating, question, answer, source_ids, provider, comment)
        VALUES (%s, %s, %s, %s, %s, %s)""",
                (rating, question, answer, list(source_ids), provider, comment))
