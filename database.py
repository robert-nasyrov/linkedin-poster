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
                status TEXT DEFAULT 'draft',  -- draft, approved, posted, rejected
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
