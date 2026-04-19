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

            CREATE TABLE IF NOT EXISTS post_stats (
                id SERIAL PRIMARY KEY,
                post_id INTEGER REFERENCES linkedin_posts(id),
                platform TEXT NOT NULL,
                platform_post_id TEXT,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS post_comments (
                id SERIAL PRIMARY KEY,
                post_id INTEGER REFERENCES linkedin_posts(id),
                platform TEXT NOT NULL,
                platform_comment_id TEXT,
                author TEXT,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(platform, platform_comment_id)
            );
        """)
        # Add columns if they don't exist (safe migration)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE linkedin_posts ADD COLUMN IF NOT EXISTS reject_reason TEXT;
                ALTER TABLE linkedin_posts ADD COLUMN IF NOT EXISTS threads_post_id TEXT;
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


async def save_post_stats(pool, post_id: int, platform: str, platform_post_id: str,
                          likes: int = 0, comments: int = 0, shares: int = 0, views: int = 0):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO post_stats (post_id, platform, platform_post_id, likes, comments, shares, views, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
               ON CONFLICT ON CONSTRAINT post_stats_pkey DO NOTHING""",
            post_id, platform, platform_post_id, likes, comments, shares, views
        )
        # Update if exists
        await conn.execute(
            """UPDATE post_stats SET likes = $1, comments = $2, shares = $3, views = $4, updated_at = NOW()
               WHERE post_id = $5 AND platform = $6""",
            likes, comments, shares, views, post_id, platform
        )


async def save_threads_post_id(pool, post_id: int, threads_post_id: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE linkedin_posts SET threads_post_id = $1 WHERE id = $2",
            threads_post_id, post_id
        )


async def get_posted_posts_for_stats(pool):
    """Get posts that have been published and need stats refresh."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, post_text, linkedin_post_id, threads_post_id, posted_at
               FROM linkedin_posts
               WHERE status = 'posted'
               AND posted_at > NOW() - INTERVAL '30 days'
               ORDER BY posted_at DESC
               LIMIT 30"""
        )
        return [dict(r) for r in rows]


async def get_top_posts(pool, limit: int = 5):
    """Get top-performing posts by total engagement (likes + comments + shares)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT lp.post_text, ps.platform, ps.likes, ps.comments, ps.shares, ps.views,
                      (ps.likes + ps.comments * 3 + ps.shares * 5) as engagement_score
               FROM post_stats ps
               JOIN linkedin_posts lp ON lp.id = ps.post_id
               WHERE ps.likes + ps.comments + ps.shares > 0
               ORDER BY engagement_score DESC
               LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


async def get_low_engagement_posts(pool, limit: int = 3, min_age_days: int = 3):
    """Posts published >N days ago that got close to zero engagement — use as anti-examples."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT lp.post_text, ps.platform, ps.likes, ps.comments, ps.shares
               FROM post_stats ps
               JOIN linkedin_posts lp ON lp.id = ps.post_id
               WHERE lp.posted_at < NOW() - INTERVAL '{int(min_age_days)} days'
                 AND (ps.likes + ps.comments + ps.shares) <= 2
               ORDER BY lp.posted_at DESC
               LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


async def save_post_comment(pool, post_id: int, platform: str, platform_comment_id: str,
                             author: str, text: str):
    """Save a comment from LinkedIn/Threads. Skips duplicates via unique constraint."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO post_comments (post_id, platform, platform_comment_id, author, text)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (platform, platform_comment_id) DO NOTHING""",
            post_id, platform, platform_comment_id, author, text
        )


async def get_recent_comments(pool, limit: int = 10, days: int = 30):
    """Get recent comments across both platforms, joined with post text for context."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT pc.platform, pc.author, pc.text, lp.post_text
               FROM post_comments pc
               JOIN linkedin_posts lp ON lp.id = pc.post_id
               WHERE pc.created_at > NOW() - INTERVAL '{int(days)} days'
               ORDER BY pc.created_at DESC
               LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]
