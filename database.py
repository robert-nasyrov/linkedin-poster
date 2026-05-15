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

            CREATE TABLE IF NOT EXISTS regen_feedback (
                id SERIAL PRIMARY KEY,
                post_id INTEGER REFERENCES linkedin_posts(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'regen',
                feedback_text TEXT NOT NULL,
                draft_text TEXT,
                source TEXT DEFAULT 'live',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_regen_feedback_created_at
                ON regen_feedback(created_at DESC);

            CREATE TABLE IF NOT EXISTS linkedin_cookies (
                id INTEGER PRIMARY KEY DEFAULT 1,
                li_at TEXT NOT NULL,
                jsessionid TEXT,
                raw_cookies TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- /talk sessions: Robert and the bot exchange Q&A; the resulting
            -- transcript becomes both the immediate post material AND a feed
            -- into build_learning_context for future generations.
            CREATE TABLE IF NOT EXISTS talk_sessions (
                id SERIAL PRIMARY KEY,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                status TEXT DEFAULT 'open',
                resulting_post_id INTEGER REFERENCES linkedin_posts(id) ON DELETE SET NULL
            );
            CREATE TABLE IF NOT EXISTS talk_messages (
                id SERIAL PRIMARY KEY,
                session_id INTEGER REFERENCES talk_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_talk_messages_session
                ON talk_messages(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_talk_sessions_finished_at
                ON talk_sessions(finished_at DESC);

            -- Robert can retire a project/topic ("TrabajaYa is dead, stop
            -- mentioning it"). Inject into prompt as DO-NOT-MENTION list.
            CREATE TABLE IF NOT EXISTS retired_topics (
                id SERIAL PRIMARY KEY,
                keyword TEXT NOT NULL UNIQUE,
                note TEXT,
                retired_at TIMESTAMPTZ DEFAULT NOW()
            );

            -- Content pillars: the 2-3 topic territories Robert wants to own
            -- in his audience's minds. Bot filters generation to stay within
            -- these — everything else is off-strategy and not worth posting.
            CREATE TABLE IF NOT EXISTS content_pillars (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                keywords TEXT,
                priority INTEGER DEFAULT 1,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # Safe migration if table existed before draft_text was added
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE regen_feedback ADD COLUMN IF NOT EXISTS draft_text TEXT;
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """)
        # Add columns if they don't exist (safe migration)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE linkedin_posts ADD COLUMN IF NOT EXISTS reject_reason TEXT;
                ALTER TABLE linkedin_posts ADD COLUMN IF NOT EXISTS threads_post_id TEXT;
                ALTER TABLE linkedin_posts ADD COLUMN IF NOT EXISTS linkedin_activity_urn TEXT;
                ALTER TABLE linkedin_cookies ADD COLUMN IF NOT EXISTS raw_cookies TEXT;
            EXCEPTION WHEN others THEN NULL;
            END $$;
        """)

        # Seed Robert's initial content pillars on first run. These are
        # opinionated defaults based on what's underleveraged in his profile:
        # "12 systems in 18 months", "Deterministic LLM Programming" as a
        # skill, and Tashkent → global market arbitrage. He can edit via
        # /pillars.
        await conn.execute("""
            DO $$
            DECLARE pillar_count INTEGER;
            BEGIN
                SELECT COUNT(*) INTO pillar_count FROM content_pillars;
                IF pillar_count = 0 THEN
                    INSERT INTO content_pillars (title, description, keywords, priority) VALUES
                    ('Production AI at real scale',
                     'War stories, incidents, architecture decisions, and metrics from running 12+ AI systems autonomously — TrabajaYa processed 3,600+ candidates. Not tutorials, not hype. Concrete numbers and what broke.',
                     'production, scale, incident, postmortem, architecture, autonomy, metrics, error handling, deployment, monitoring',
                     1),
                    ('Deterministic LLM Programming',
                     'The methodology of building LLM-powered systems that behave reliably and reproducibly. Patterns, anti-patterns, prompting discipline, output validation, schema enforcement, fallback design. This is Robert''s skill brand.',
                     'deterministic, reproducible, schema, validation, structured output, JSON, retries, idempotency, prompt patterns',
                     1),
                    ('Tashkent → global market arbitrage',
                     'Building AI work from Tashkent for English-speaking / LATAM clients. The visible-from-here truth about timezone bias, hiring filters, contracting from non-SF locations, and what arbitrage actually looks like.',
                     'remote, timezone, Tashkent, hiring bias, arbitrage, geography, freelance, contracts, English market',
                     1);
                END IF;
            END $$;
        """)

        # post_stats was originally INSERT-only with a pkey-on-id ON CONFLICT clause
        # that never triggered, so every save_post_stats call appended a row. Clean
        # up duplicates (keep the most recent per post+platform) and add a real
        # unique constraint so future writes UPSERT cleanly.
        await conn.execute("""
            DELETE FROM post_stats
            WHERE id NOT IN (
                SELECT MAX(id) FROM post_stats GROUP BY post_id, platform
            );
        """)
        await conn.execute("""
            DO $$ BEGIN
                ALTER TABLE post_stats
                    ADD CONSTRAINT post_stats_post_platform_unique
                    UNIQUE (post_id, platform);
            EXCEPTION WHEN duplicate_object THEN NULL;
            WHEN others THEN NULL;
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
    """Recent rejected posts WITH a written reason — empty/null rejects are noise."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT post_text, reject_reason FROM linkedin_posts
               WHERE status = 'rejected'
                 AND reject_reason IS NOT NULL
                 AND TRIM(reject_reason) <> ''
               ORDER BY created_at DESC LIMIT $1""",
            limit
        )
        return [{"text": r["post_text"], "reason": r["reject_reason"]} for r in rows]


async def save_regen_feedback(pool, post_id: int, feedback_text: str,
                               draft_text: str = None):
    """Save regenerate feedback together with the EXACT draft Robert was reacting to.
    The post_text on linkedin_posts is overwritten on regen, so we snapshot here."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO regen_feedback
               (post_id, kind, feedback_text, draft_text, source)
               VALUES ($1, 'regen', $2, $3, 'live')""",
            post_id, feedback_text, draft_text
        )


async def get_regen_feedback(pool, limit: int = 10, days: int = 60):
    """Recent regenerate feedback paired with the original draft text Robert saw.
    Falls back to current linkedin_posts.post_text if draft_text missing (legacy rows)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT rf.feedback_text,
                       rf.created_at,
                       COALESCE(rf.draft_text, lp.post_text) AS draft_text
                FROM regen_feedback rf
                LEFT JOIN linkedin_posts lp ON lp.id = rf.post_id
                WHERE rf.created_at > NOW() - INTERVAL '{int(days)} days'
                ORDER BY rf.created_at DESC LIMIT $1""",
            limit
        )
        return [dict(r) for r in rows]


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
    """UPSERT one row per (post_id, platform). Older revisions of this code
    inserted a fresh row on every call because the ON CONFLICT clause was
    bound to the wrong constraint — that's been fixed by adding a unique
    index on (post_id, platform) in init_db."""
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO post_stats
                   (post_id, platform, platform_post_id, likes, comments, shares, views, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
               ON CONFLICT (post_id, platform) DO UPDATE SET
                   platform_post_id = EXCLUDED.platform_post_id,
                   likes = EXCLUDED.likes,
                   comments = EXCLUDED.comments,
                   shares = EXCLUDED.shares,
                   views = EXCLUDED.views,
                   updated_at = NOW()""",
            post_id, platform, platform_post_id, likes, comments, shares, views,
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
            """SELECT id, post_text, linkedin_post_id, threads_post_id,
                      linkedin_activity_urn, posted_at
               FROM linkedin_posts
               WHERE status = 'posted'
               AND posted_at > NOW() - INTERVAL '30 days'
               ORDER BY posted_at DESC
               LIMIT 30"""
        )
        return [dict(r) for r in rows]


async def set_linkedin_activity_urn(pool, post_id: int, activity_urn: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE linkedin_posts SET linkedin_activity_urn = $1 WHERE id = $2",
            activity_urn, post_id
        )


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


async def open_talk_session(pool) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO talk_sessions (started_at, status)
               VALUES (NOW(), 'open') RETURNING id"""
        )
        return row["id"]


async def append_talk_message(pool, session_id: int, role: str, text: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO talk_messages (session_id, role, text)
               VALUES ($1, $2, $3)""",
            session_id, role, text
        )


async def get_talk_messages(pool, session_id: int) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT role, text, created_at FROM talk_messages
               WHERE session_id = $1 ORDER BY id""",
            session_id
        )
        return [dict(r) for r in rows]


async def close_talk_session(pool, session_id: int, status: str = "drafted",
                              post_id: int = None):
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE talk_sessions
               SET finished_at = NOW(), status = $2, resulting_post_id = $3
               WHERE id = $1""",
            session_id, status, post_id
        )


async def get_content_pillars(pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, title, description, keywords, priority FROM content_pillars
               ORDER BY priority, id"""
        )
        return [dict(r) for r in rows]


async def add_content_pillar(pool, title: str, description: str = None,
                              keywords: str = None, priority: int = 1):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO content_pillars (title, description, keywords, priority)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            title.strip(), description, keywords, priority,
        )
        return row["id"]


async def remove_content_pillar(pool, pillar_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM content_pillars WHERE id = $1", pillar_id
        )
        return result.endswith(" 1")


async def add_retired_topic(pool, keyword: str, note: str = None):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO retired_topics (keyword, note)
               VALUES ($1, $2)
               ON CONFLICT (keyword) DO UPDATE SET note = EXCLUDED.note,
                                                    retired_at = NOW()""",
            keyword.strip(), note
        )


async def remove_retired_topic(pool, keyword: str):
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM retired_topics WHERE LOWER(keyword) = LOWER($1)",
            keyword.strip()
        )
        return result.endswith(" 1")


async def get_retired_topics(pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT keyword, note, retired_at FROM retired_topics ORDER BY retired_at DESC"
        )
        return [dict(r) for r in rows]


async def get_recent_talk_transcripts(pool, limit: int = 3, days: int = 30):
    """Recent finished talk sessions — fed into build_learning_context as a
    'Robert literally said these things this week' source of authentic material."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT id, started_at, status FROM talk_sessions
                WHERE finished_at IS NOT NULL
                  AND finished_at > NOW() - INTERVAL '{int(days)} days'
                  AND status IN ('drafted', 'posted', 'used')
                ORDER BY finished_at DESC LIMIT $1""",
            limit
        )
        sessions = []
        for r in rows:
            msgs = await conn.fetch(
                """SELECT role, text FROM talk_messages
                   WHERE session_id = $1 ORDER BY id""",
                r["id"]
            )
            sessions.append({
                "session_id": r["id"],
                "started_at": r["started_at"],
                "messages": [dict(m) for m in msgs],
            })
        return sessions


async def save_linkedin_cookies(pool, li_at: str, jsessionid: str = None,
                                 raw_cookies: str = None):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO linkedin_cookies (id, li_at, jsessionid, raw_cookies, updated_at)
               VALUES (1, $1, $2, $3, NOW())
               ON CONFLICT (id) DO UPDATE
               SET li_at = $1, jsessionid = $2, raw_cookies = $3, updated_at = NOW()""",
            li_at, jsessionid, raw_cookies
        )


async def get_linkedin_cookies(pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM linkedin_cookies WHERE id = 1")
        return dict(row) if row else None


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
