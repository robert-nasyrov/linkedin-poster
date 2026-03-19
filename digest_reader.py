"""
Reads context from multiple sources:
1. Digest bot PostgreSQL (daily_summaries, open_items, life_context)
2. Telegram channel messages via Telethon
"""
import os
import logging
from datetime import datetime, timedelta, timezone

import asyncpg
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_STRING_SESSION, DIGEST_CHANNEL
from database import save_digest

logger = logging.getLogger(__name__)

DIGEST_DATABASE_URL = os.getenv("DIGEST_DATABASE_URL", "")


# ==================== DIGEST BOT DATABASE ====================

async def get_digest_context() -> str:
    """Pull rich context from digest bot's PostgreSQL: summaries, open items, life context."""
    if not DIGEST_DATABASE_URL:
        logger.warning("DIGEST_DATABASE_URL not set — skipping digest DB")
        return ""

    try:
        conn = await asyncpg.connect(DIGEST_DATABASE_URL, timeout=10)
    except Exception as e:
        logger.error(f"Cannot connect to digest DB: {e}")
        return ""

    sections = []

    try:
        # Last 3 daily summaries
        summaries = await conn.fetch("""
            SELECT summary_date, digest_text, key_items, action_items
            FROM daily_summaries
            ORDER BY summary_date DESC
            LIMIT 3
        """)
        if summaries:
            parts = []
            for s in summaries:
                date_str = s["summary_date"].strftime("%Y-%m-%d") if s["summary_date"] else "?"
                text = s.get("digest_text") or ""
                actions = s.get("action_items") or ""
                entry = f"[{date_str}]\n{text}"
                if actions:
                    entry += f"\nAction items: {actions}"
                parts.append(entry)
            sections.append("=== RECENT DAILY DIGESTS ===\n" + "\n---\n".join(parts))

        # Open items (tasks/threads Robert is tracking)
        open_items = await conn.fetch("""
            SELECT item_text, source, priority
            FROM open_items
            WHERE status = 'open' OR status IS NULL
            ORDER BY priority DESC NULLS LAST
            LIMIT 15
        """)
        if open_items:
            items = [f"- [{r.get('priority', '?')}] {r['item_text']} (from: {r.get('source', '?')})" for r in open_items]
            sections.append("=== OPEN ITEMS / ACTIVE THREADS ===\n" + "\n".join(items))

        # Life context (persistent facts about Robert's situation)
        life_ctx = await conn.fetch("""
            SELECT context_key, context_value
            FROM life_context
            ORDER BY updated_at DESC
            LIMIT 15
        """)
        if life_ctx:
            ctx = [f"- {r['context_key']}: {r['context_value']}" for r in life_ctx]
            sections.append("=== LIFE CONTEXT ===\n" + "\n".join(ctx))

    except Exception as e:
        logger.error(f"Error reading digest DB: {e}")
    finally:
        await conn.close()

    result = "\n\n".join(sections)
    logger.info(f"Loaded digest context: {len(result)} chars, {len(sections)} sections")
    return result


# ==================== TELEGRAM CHANNEL ====================

def get_telethon_client():
    return TelegramClient(
        StringSession(TELEGRAM_STRING_SESSION),
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH
    )


async def fetch_recent_digests(pool, hours: int = 24):
    """Fetch messages from digest channel posted in the last N hours."""
    client = get_telethon_client()
    await client.connect()

    if not await client.is_user_authorized():
        logger.error("Telethon client not authorized. Check STRING_SESSION.")
        await client.disconnect()
        return []

    try:
        entity = await client.get_entity(DIGEST_CHANNEL)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        messages = []

        async for msg in client.iter_messages(entity, limit=50):
            if msg.date < cutoff:
                break
            if not msg.text:
                continue
            if len(msg.text) < 50:
                continue

            await save_digest(pool, DIGEST_CHANNEL, msg.id, msg.text, msg.date)
            messages.append({
                "id": msg.id,
                "text": msg.text,
                "date": msg.date.isoformat()
            })

        logger.info(f"Fetched {len(messages)} digests from {DIGEST_CHANNEL}")
        return messages

    except Exception as e:
        logger.error(f"Error fetching digests: {e}")
        return []
    finally:
        await client.disconnect()


async def fetch_digests_for_post(pool):
    """
    Build rich context for post generation:
    1. Digest bot DB (summaries, open items, life context)
    2. Unprocessed Telegram channel messages
    Returns (combined_text, digest_ids) or empty string if nothing found.
    """
    from database import get_unprocessed_digests

    parts = []
    all_digest_ids = []

    # Source 1: Digest bot database
    db_context = await get_digest_context()
    if db_context:
        parts.append(db_context)

    # Source 2: Telegram channel messages
    tg_digests = await get_unprocessed_digests(pool, limit=5)
    if tg_digests:
        tg_text = "\n\n---\n\n".join([
            f"[{d['date'].strftime('%Y-%m-%d')}]\n{d['text']}"
            for d in tg_digests
        ])
        parts.append("=== TELEGRAM CHANNEL UPDATES ===\n" + tg_text)
        all_digest_ids = [d["id"] for d in tg_digests]

    if not parts:
        return ""

    combined = "\n\n".join(parts)
    return combined, all_digest_ids
