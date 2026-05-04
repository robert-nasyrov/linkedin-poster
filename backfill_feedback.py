"""
Backfill regenerate feedback from Telegram chat history with the bot.

Reads the admin's chat with the bot via Telethon, finds the pattern:
  bot: "📝 *Draft #N*"
  ...later...
  bot: "🔄 What didn't work about this post?"
  user: <text>          ← this is the feedback we need
and links each feedback to post_id N.

Reject reasons are already in linkedin_posts.reject_reason — skipped.

Run:
    railway run python backfill_feedback.py            # writes preview to backfill_preview.json
    railway run python backfill_feedback.py --commit   # actually inserts into regen_feedback
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import asyncpg
from telethon import TelegramClient
from telethon.sessions import StringSession


REGEN_PROMPT_MARKER = "What didn't work about this post"
REJECT_PROMPT_MARKER = "Why? Send a short reason"
REGEN_ACK_MARKER = "Got it. Regenerating with your feedback"

DRAFT_RE = re.compile(r"Draft\s*#?\s*(\d+)", re.IGNORECASE)


def _extract_draft(text: str) -> tuple[int | None, str | None]:
    """Find 'Draft #N' anywhere in a bot message, return (id, everything-after-header)."""
    m = DRAFT_RE.search(text)
    if not m:
        return None, None
    # Body: everything after the matched header, with leading whitespace/asterisks stripped
    rest = text[m.end():]
    rest = re.sub(r"^[\s*_`]+", "", rest)
    return int(m.group(1)), (rest.strip() or None)


def _serialize(items):
    return [
        {
            "post_id": x["post_id"],
            "kind": x["kind"],
            "text": x["text"],
            "draft_text": x.get("draft_text"),
            "date": x["date"].isoformat(),
            "msg_id": x["msg_id"],
        }
        for x in items
    ]


async def main(commit: bool):
    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    string_session = os.getenv("TELEGRAM_STRING_SESSION", "")
    admin_id = int(os.getenv("TELEGRAM_ADMIN_ID", "0") or 0)
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    # Prefer public URL: DATABASE_URL points at *.railway.internal which only
    # resolves from inside Railway's runtime, not from a local machine.
    db_url = (
        os.getenv("DATABASE_PUBLIC_URL")
        or os.getenv("DATABASE_URL", "")
    )

    missing = [k for k, v in {
        "TELEGRAM_API_ID": api_id,
        "TELEGRAM_API_HASH": api_hash,
        "TELEGRAM_STRING_SESSION": string_session,
        "TELEGRAM_ADMIN_ID": admin_id,
        "TELEGRAM_BOT_TOKEN": bot_token,
        "DATABASE_URL": db_url,
    }.items() if not v]
    if missing:
        print(f"ERROR: missing env: {missing}")
        print("Run via: railway run python backfill_feedback.py")
        return

    bot_user_id = int(bot_token.split(":")[0])

    # Connect Telegram
    client = TelegramClient(StringSession(string_session), api_id, api_hash)
    await client.start()
    print(f"Telethon connected. Resolving bot uid={bot_user_id} via dialogs ...")

    # Telethon needs to have seen the entity first. Iterating dialogs populates the cache.
    chat = None
    async for dialog in client.iter_dialogs():
        ent = dialog.entity
        if getattr(ent, "id", None) == bot_user_id:
            chat = ent
            break
    if chat is None:
        print(f"ERROR: no dialog found with bot uid={bot_user_id}")
        print("Make sure you've talked to the bot at least once from this user account.")
        await client.disconnect()
        return
    print(f"Found chat: {getattr(chat, 'username', None) or chat.id}")

    # Pull all messages oldest-first
    msgs = []
    async for m in client.iter_messages(chat, reverse=True, limit=None):
        msgs.append(m)
    print(f"Loaded {len(msgs)} messages from chat history")

    # Walk forward, tracking the latest draft (id + body text) and the pending fb prompt
    found = []
    drafts_by_id: dict[int, str] = {}  # post_id -> last seen draft body text
    current_draft_id = None            # most recent Draft #N
    awaiting_regen_for = None          # post_id waiting feedback for
    awaiting_draft_text = None         # the actual text Robert was looking at

    # Counters to debug what's matching
    n_drafts_seen = 0
    n_prompts_seen = 0
    n_acks_seen = 0

    for m in msgs:
        text = m.text or m.message or ""
        if not text:
            continue

        is_bot = m.sender_id == bot_user_id
        is_admin = m.sender_id == admin_id

        if is_bot:
            # New draft posted → snapshot text, remember id, reset tracking
            pid, body = _extract_draft(text)
            if pid is not None:
                n_drafts_seen += 1
                current_draft_id = pid
                if body:
                    drafts_by_id[pid] = body
                awaiting_regen_for = None
                awaiting_draft_text = None
                continue

            # Bot asked for regen feedback → next admin text is the answer
            if REGEN_PROMPT_MARKER in text:
                n_prompts_seen += 1
                # The "What didn't work…" reply is a reply_to the draft message.
                # Resolve via reply_to_msg_id when possible — otherwise fall back to last draft.
                target_id = current_draft_id
                target_body = drafts_by_id.get(current_draft_id)
                if m.reply_to and m.reply_to.reply_to_msg_id:
                    try:
                        replied = await client.get_messages(
                            chat, ids=m.reply_to.reply_to_msg_id
                        )
                        if replied and replied.text:
                            pid2, body2 = _extract_draft(replied.text)
                            if pid2 is not None:
                                target_id = pid2
                                if body2:
                                    target_body = body2
                                    drafts_by_id[pid2] = body2
                    except Exception:
                        pass
                awaiting_regen_for = target_id
                awaiting_draft_text = target_body
                continue

            if REGEN_ACK_MARKER in text:
                n_acks_seen += 1
                awaiting_regen_for = None
                awaiting_draft_text = None
                continue

        elif is_admin:
            if awaiting_regen_for is not None:
                if text.strip().startswith("/"):
                    awaiting_regen_for = None
                    awaiting_draft_text = None
                    continue
                found.append({
                    "post_id": awaiting_regen_for,
                    "kind": "regen",
                    "text": text.strip(),
                    "draft_text": awaiting_draft_text,
                    "date": m.date.astimezone(timezone.utc),
                    "msg_id": m.id,
                })
                awaiting_regen_for = None
                awaiting_draft_text = None

    print(f"\nDraft headers seen: {n_drafts_seen}  "
          f"Regen prompts: {n_prompts_seen}  "
          f"Regen acks: {n_acks_seen}")
    print(f"Found {len(found)} regenerate feedbacks in chat history.")
    has_draft = sum(1 for x in found if x.get("draft_text"))
    print(f"  with original draft text recovered: {has_draft}/{len(found)}")
    if found:
        print("Preview (last 10):")
        for x in found[-10:]:
            d_marker = "✓" if x.get("draft_text") else "✗"
            print(f"  [{x['date'].date()}] #{x['post_id']:3d} {d_marker} draft  "
                  f"FB: {x['text'][:80]}")

    # Save preview JSON either way
    with open("backfill_preview.json", "w", encoding="utf-8") as f:
        json.dump(_serialize(found), f, ensure_ascii=False, indent=2)
    print("Preview written to backfill_preview.json")

    if not commit:
        print("\nDry run — nothing written to DB. Re-run with --commit to insert.")
        await client.disconnect()
        return

    # Commit to DB
    conn = await asyncpg.connect(db_url, timeout=15)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS regen_feedback (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES linkedin_posts(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            feedback_text TEXT NOT NULL,
            draft_text TEXT,
            source TEXT DEFAULT 'live',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_regen_feedback_post_id ON regen_feedback(post_id);
        CREATE INDEX IF NOT EXISTS idx_regen_feedback_created_at ON regen_feedback(created_at DESC);
    """)
    # Migrate older deployments where draft_text didn't exist
    await conn.execute("""
        DO $$ BEGIN
            ALTER TABLE regen_feedback ADD COLUMN IF NOT EXISTS draft_text TEXT;
        EXCEPTION WHEN others THEN NULL;
        END $$;
    """)

    # Idempotent insert: skip rows that already exist (same post_id + text)
    inserted = 0
    skipped = 0
    for x in found:
        # Verify post_id exists to avoid FK errors from orphan refs in old chats
        exists = await conn.fetchval(
            "SELECT 1 FROM linkedin_posts WHERE id = $1", x["post_id"]
        )
        if not exists:
            skipped += 1
            continue
        # Avoid duplicates
        dup = await conn.fetchval(
            """SELECT 1 FROM regen_feedback
               WHERE post_id = $1 AND feedback_text = $2""",
            x["post_id"], x["text"]
        )
        if dup:
            skipped += 1
            continue
        await conn.execute(
            """INSERT INTO regen_feedback
               (post_id, kind, feedback_text, draft_text, source, created_at)
               VALUES ($1, $2, $3, $4, 'telegram_backfill', $5)""",
            x["post_id"], x["kind"], x["text"], x.get("draft_text"), x["date"]
        )
        inserted += 1

    print(f"\nInserted {inserted} new rows. Skipped {skipped} (orphan or duplicate).")
    await conn.close()
    await client.disconnect()


if __name__ == "__main__":
    commit = "--commit" in sys.argv
    asyncio.run(main(commit))
