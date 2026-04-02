import asyncpg
import json
from datetime import datetime
from config import DATABASE_URL


async def get_pool():
    return await asyncpg.create_pool(DATABASE_URL)


async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS digests (
                id SERIAL PRIMARY KEY,
                channel TEXT NOT NULL,
                message_id BIGINT NOT NULL UNIQUE,
                text TEXT NOT NULL,
                date TIMESTAMPTZ NOT NULL,
                processed BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS linkedin_posts (
                id SERIAL PRIMARY KEY,
                digest_ids INTEGER[] DEFAULT '{}',
                post_text TEXT NOT NULL,
                meme_suggestion JSONB,
                status TEXT DEFAULT 'draft',
                reject_reason TEXT,
                linkedin_post_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                posted_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS linkedin_tokens (
                id INTEGER PRIMARY KEY DEFAULT 1,
                access_token TEXT NOT NULL,
                expires_at TIMESTAMPTZ,
                person_urn TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS user_context (
                id SERIAL PRIMARY KEY,
                context_text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS threads_tokens (
                id INTEGER PRIMARY KEY DEFAULT 1,
                access_token TEXT NOT NULL,
                user_id TEXT NOT NULL,
                expires_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # Add columns if they don't exist (safe migration)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE linkedin_posts ADD COLUMN IF NOT EXISTS reject_reason TEXT;
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """)


async def save_digest(pool, channel: str, message_id: int, text: str, date: datetime):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO digests (channel, message_id, text, date)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (message_id) DO NOTHING""",
            channel, message_id, text, date
        )


async def get_unprocessed_digests(pool, limit: int = 10):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, text, date FROM digests
               WHERE processed = FALSE
               ORDER BY date DESC LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


async def mark_digests_processed(pool, ids: list):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE digests SET processed = TRUE WHERE id = ANY($1)",
            ids
        )


async def save_post(pool, digest_ids: list, post_text: str, meme_suggestion: dict = None):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO linkedin_posts (digest_ids, post_text, meme_suggestion)
               VALUES ($1, $2, $3) RETURNING id""",
            digest_ids, post_text, json.dumps(meme_suggestion) if meme_suggestion else None
        )
        return row["id"]


async def update_post_status(pool, post_id: int, status: str, linkedin_post_id: str = None):
    async with pool.acquire() as conn:
        if linkedin_post_id:
            await conn.execute(
                """UPDATE linkedin_posts
                   SET status = $1, linkedin_post_id = $2, posted_at = NOW()
                   WHERE id = $3""",
                status, linkedin_post_id, post_id
            )
        else:
            await conn.execute(
                "UPDATE linkedin_posts SET status = $1 WHERE id = $2",
                status, post_id
            )


async def update_post_text(pool, post_id: int, new_text: str, meme_suggestion: dict = None):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE linkedin_posts SET post_text = $1, meme_suggestion = $2 WHERE id = $3",
            new_text, json.dumps(meme_suggestion) if meme_suggestion else None, post_id
        )


async def get_post(pool, post_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM linkedin_posts WHERE id = $1", post_id)
        return dict(row) if row else None


async def save_linkedin_token(pool, access_token: str, expires_at: datetime, person_urn: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO linkedin_tokens (id, access_token, expires_at, person_urn, updated_at)
               VALUES (1, $1, $2, $3, NOW())
               ON CONFLICT (id) DO UPDATE
               SET access_token = $1, expires_at = $2, person_urn = $3, updated_at = NOW()""",
            access_token, expires_at, person_urn
        )


async def get_linkedin_token(pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM linkedin_tokens WHERE id = 1")
        return dict(row) if row else None


async def get_approved_posts(pool, limit: int = 5):
    """Get recent approved/posted posts as positive examples."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT post_text FROM linkedin_posts
               WHERE status IN ('posted', 'approved')
               ORDER BY created_at DESC LIMIT $1""",
            limit
        )
        return [r["post_text"] for r in rows]


async def get_rejected_posts(pool, limit: int = 5):
    """Get recent rejected posts with reasons as negative examples."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT post_text, reject_reason FROM linkedin_posts
               WHERE status = 'rejected'
               ORDER BY created_at DESC LIMIT $1""",
            limit
        )
        return [{"text": r["post_text"], "reason": r["reject_reason"] or "no reason given"} for r in rows]


async def set_reject_reason(pool, post_id: int, reason: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE linkedin_posts SET reject_reason = $1 WHERE id = $2",
            reason, post_id
        )


async def add_user_context(pool, text: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_context (context_text) VALUES ($1)",
            text
        )


async def get_user_context(pool, limit: int = 20):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT context_text, created_at FROM user_context ORDER BY created_at DESC LIMIT $1",
            limit
        )
        return [{"text": r["context_text"], "date": r["created_at"].strftime("%Y-%m-%d")} for r in rows]


async def save_threads_token(pool, access_token: str, user_id: str, expires_in: int = 5184000):
    from datetime import timedelta
    expires_at = datetime.now() + timedelta(seconds=expires_in)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO threads_tokens (id, access_token, user_id, expires_at, updated_at)
               VALUES (1, $1, $2, $3, NOW())
               ON CONFLICT (id) DO UPDATE
               SET access_token = $1, user_id = $2, expires_at = $3, updated_at = NOW()""",
            access_token, user_id, expires_at
        )


async def get_threads_token(pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM threads_tokens WHERE id = 1")
        return dict(row) if row else None
