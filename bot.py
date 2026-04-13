"""
LinkedIn Auto-Poster Bot
- Reads digests from Telegram channels
- Generates LinkedIn posts via Claude API
- Sends for approval via Telegram inline buttons
- Posts to LinkedIn on approval
- Includes OAuth flow for LinkedIn token setup
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    URLInputFile,
)
from aiogram.filters import Command
from aiohttp import web

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID,
    LINKEDIN_CLIENT_ID, POST_DAYS, POST_HOUR,
)
from database import (
    get_pool, init_db, save_post, update_post_status,
    update_post_text, get_post, mark_digests_processed,
    save_linkedin_token, get_linkedin_token,
    save_threads_token, get_threads_token,
)
from digest_reader import fetch_recent_digests, fetch_digests_for_post
from post_generator import generate_post_from_digest, generate_post_from_topic
from linkedin_api import (
    get_auth_url, exchange_code, post_to_linkedin, check_token_valid,
)
from comment_engine import find_linkedin_posts, generate_comment, generate_comment_from_url
from threads_api import (
    get_threads_auth_url, exchange_threads_code, post_to_threads,
    adapt_post_for_threads, generate_threads_content, post_thread_chain,
)
from stats_tracker import collect_all_stats

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

pool = None


async def get_threads_token_or_env(pool):
    """Get Threads token from DB, fallback to env vars."""
    import os
    try:
        from database import get_threads_token as _get_threads_token_db
        data = await _get_threads_token_db(pool)
        if data:
            return data
    except Exception as e:
        logger.warning(f"Threads DB token check failed: {e}")
    token = os.getenv("THREADS_ACCESS_TOKEN", "")
    user_id = os.getenv("THREADS_USER_ID", "")
    if token and user_id:
        return {"access_token": token, "user_id": user_id, "expires_at": None}
    return None


# ==================== TELEGRAM COMMANDS ====================

@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    await message.answer(
        "🚀 *LinkedIn + Threads Auto-Poster Bot*\n\n"
        "Posts:\n"
        "/generate — Auto-generate post from your life context\n"
        "/write <topic> — Write post from your thought\n"
        "/twrite <topic> — Write for Threads (thread chains)\n"
        "/post <text> — Post ready text directly (no AI)\n\n"
        "Comments:\n"
        "/find — Find posts to comment on\n"
        "/comment <url> — Generate comment for a post\n\n"
        "Settings:\n"
        "/context <update> — Add context about your life/work\n"
        "/stats — See top performing posts\n"
        "/connect — Connect LinkedIn account\n"
        "/threads — Connect Threads account\n"
        "/status — Check bot & token status\n",
        parse_mode="Markdown"
    )


@router.message(Command("connect"))
async def cmd_connect(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    if not LINKEDIN_CLIENT_ID:
        await message.answer("❌ LINKEDIN_CLIENT_ID not set. Check .env")
        return
    url = get_auth_url()
    await message.answer(
        f"🔗 Click to connect LinkedIn:\n\n{url}\n\n"
        "After authorizing, you'll be redirected back and the token will be saved automatically.",
    )


@router.message(Command("threads"))
async def cmd_threads(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    url = get_threads_auth_url()
    await message.answer(
        f"🧵 Click to connect Threads:\n\n{url}\n\n"
        "After authorizing, the token will be saved automatically.",
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    token_data = await get_linkedin_token(pool)
    threads_data = await get_threads_token_or_env(pool)
    
    lines = ["*Bot Status*\n"]
    
    if token_data:
        valid = await check_token_valid(token_data["access_token"])
        expires = token_data["expires_at"].strftime("%Y-%m-%d")
        status = "✅ Valid" if valid else "❌ Expired"
        lines.append(f"LinkedIn: {status} (expires {expires})")
    else:
        lines.append("LinkedIn: ❌ Not connected (/connect)")
    
    if threads_data:
        expires = threads_data["expires_at"].strftime("%Y-%m-%d") if threads_data.get("expires_at") else "?"
        lines.append(f"Threads: ✅ Connected (expires {expires})")
    else:
        lines.append("Threads: ❌ Not connected (/threads)")
    
    lines.append(f"\nSchedule: days {POST_DAYS}, hour {POST_HOUR}:00 UZT")
    
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Show top performing posts."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    
    from database import get_top_posts
    top = await get_top_posts(pool, limit=5)
    
    if not top:
        await message.answer("📊 No stats yet. Posts will be tracked after publishing. Stats collected daily at 21:00.")
        return
    
    lines = ["📊 Top Performing Posts:\n"]
    for i, t in enumerate(top):
        platform = t.get("platform", "?")
        likes = t.get("likes", 0)
        comments = t.get("comments", 0)
        shares = t.get("shares", 0)
        views = t.get("views", 0)
        text = t.get("post_text", "")[:100]
        lines.append(f"{i+1}. [{platform}] {likes}❤️ {comments}💬 {shares}🔄 {views}👁\n   {text}...")
    
    await message.answer("\n".join(lines))


@router.message(Command("fetch"))
async def cmd_fetch(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    await message.answer("📡 Fetching digests...")
    await fetch_recent_digests(pool, hours=48)
    await message.answer("✅ Done")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Show top-performing posts."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    from database import get_top_posts
    top = await get_top_posts(pool, limit=5)

    if not top:
        # Try collecting stats now
        await message.answer("📊 No stats yet. Collecting now...")
        li_token = await get_linkedin_token(pool)
        threads_data = await get_threads_token_or_env(pool)
        li_access = li_token["access_token"] if li_token else None
        th_access = threads_data["access_token"] if threads_data else None
        updated = await collect_all_stats(pool, li_access, th_access)
        if updated:
            top = await get_top_posts(pool, limit=5)

    if not top:
        await message.answer("📊 No engagement data yet. Stats collect daily at 21:00.")
        return

    lines = ["📊 Top performing posts:\n"]
    for i, p in enumerate(top):
        platform = p["platform"].upper()
        text_preview = p["post_text"][:80].replace("\n", " ")
        lines.append(
            f"{i+1}. [{platform}] {p['likes']}❤️ {p['comments']}💬 {p['shares']}🔄"
            f"\n   {text_preview}..."
        )

    await message.answer("\n\n".join(lines))


@router.message(Command("generate"))
async def cmd_generate(message: Message):
    """Auto-generate a post from Robert's current life context — no topic needed."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    await message.answer("🧠 Reading your life context and generating post...")

    try:
        from post_generator import build_learning_context
        from digest_reader import get_digest_context

        # Gather all context
        life_context = await get_digest_context()
        learning = await build_learning_context(pool)
        
        combined = ""
        if life_context:
            combined += f"{life_context}\n\n"
        if learning:
            combined += f"{learning}\n\n"

        if not combined.strip():
            await message.answer("❌ No life context found. Use /write <topic> instead, or add context with /context")
            return

        generated = await generate_post_from_digest(combined, pool=pool)
        post_id = await save_post(pool, [], generated["post_text"], generated.get("meme"))
        await send_approval(message.chat.id, post_id, generated)
    except Exception as e:
        logger.error(f"Generation error: {e}")
        await message.answer(f"❌ Error generating post: {e}")


@router.message(Command("write"))
async def cmd_write(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    topic = message.text.replace("/write", "", 1).strip()
    if not topic:
        await message.answer("Usage: /write <your thought or topic>")
        return

    await message.answer("🧠 Generating post from your thought...")
    try:
        generated = await generate_post_from_topic(topic, pool=pool)
        post_id = await save_post(pool, [], generated["post_text"], generated["meme"])
        await send_approval(message.chat.id, post_id, generated)
    except Exception as e:
        logger.error(f"Generation error: {e}")
        await message.answer(f"❌ Error: {e}")


@router.message(Command("post"))
async def cmd_post(message: Message):
    """Post text directly to LinkedIn without AI generation. Supports photo attachment."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    
    # Text can be in message.text (plain) or message.caption (with photo)
    raw = message.text or message.caption or ""
    text = raw.replace("/post", "", 1).strip()
    
    if not text:
        await message.answer("Usage: /post <ready text to publish on LinkedIn>\nCan also attach a photo!")
        return

    # If photo attached, download and save URL for LinkedIn upload
    meme_data = None
    if message.photo:
        photo = message.photo[-1]  # highest resolution
        file = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
        meme_data = {"source": "telegram_photo", "image_url": file_url}

    post_id = await save_post(pool, [], text, meme_data)
    generated = {"post_text": text, "meme": meme_data}
    await send_approval(message.chat.id, post_id, generated)


# ==================== COMMENT COMMANDS ====================

@router.message(Command("find"))
async def cmd_find(message: Message):
    """Find relevant LinkedIn posts to comment on."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    custom_topic = (message.text or "").replace("/find", "", 1).strip()
    
    if custom_topic:
        await message.answer(f"🔍 Searching LinkedIn for: {custom_topic}...")
    else:
        await message.answer("🔍 Searching for relevant LinkedIn posts...")

    posts = await find_linkedin_posts(count=5, custom_topic=custom_topic or None)
    if not posts:
        await message.answer("❌ No posts found. Try again later.")
        return

    for i, post in enumerate(posts):
        url = post.get("url", "")
        author = post.get("author", "Unknown")
        summary = post.get("summary", "")[:300]
        date = post.get("date", "")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Generate Comment", callback_data=f"gencomment:{i}"),
                InlineKeyboardButton(text="⏭ Skip", callback_data=f"skippost:{i}"),
            ],
        ])

        date_line = f"\n📅 {date}" if date and date != "unknown" else ""
        await message.answer(
            f"📌 Post {i+1}/{len(posts)}\n"
            f"👤 {author}{date_line}\n"
            f"📝 {summary}\n"
            f"🔗 {url}",
            reply_markup=keyboard
        )

    # Store posts in memory for callback handlers
    found_posts_cache[message.from_user.id] = posts


@router.message(Command("comment"))
async def cmd_comment(message: Message):
    """Generate a comment for a specific LinkedIn post URL."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    text = (message.text or "").replace("/comment", "", 1).strip()
    if not text or "linkedin.com" not in text:
        await message.answer("Usage: /comment <linkedin post URL>")
        return

    await message.answer("🧠 Reading post and generating comment...")

    result = await generate_comment_from_url(text)
    comment_text = result.get("comment", "")
    summary = result.get("summary", "")

    if not comment_text:
        await message.answer("❌ Could not generate comment. Try a different URL.")
        return

    # Store for approval
    pending_comments[message.from_user.id] = {
        "url": text,
        "comment": comment_text,
        "summary": summary,
    }

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Post Comment", callback_data="postcomment"),
            InlineKeyboardButton(text="🔄 Regenerate", callback_data="regencomment"),
        ],
        [
            InlineKeyboardButton(text="✏️ Edit", callback_data="editcomment"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancelcomment"),
        ],
    ])

    await message.answer(
        f"📌 Post: {summary[:200]}\n\n"
        f"💬 Comment:\n{comment_text}",
        reply_markup=keyboard
    )


# ==================== THREADS COMMANDS ====================

@router.message(Command("twrite"))
async def cmd_twrite(message: Message):
    """Generate Threads-native content (single post or thread chain)."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    topic = (message.text or "").replace("/twrite", "", 1).strip()
    if not topic:
        await message.answer("Usage: /twrite <topic or thought>")
        return

    threads_data = await get_threads_token_or_env(pool)
    if not threads_data:
        await message.answer("❌ Threads not connected. Use /threads or set THREADS_ACCESS_TOKEN")
        return

    await message.answer("🧵 Generating Threads content...")

    content = await generate_threads_content(topic)
    parts = content.get("parts", [])
    fmt = content.get("format", "single")

    if not parts:
        await message.answer("❌ Failed to generate content.")
        return

    # Store for approval
    pending_threads_content[message.from_user.id] = {
        "parts": parts,
        "format": fmt,
    }

    # Show preview
    if fmt == "thread" and len(parts) > 1:
        preview = "\n\n---\n\n".join(parts)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🧵 Post Thread ({len(parts)} parts)", callback_data="postthread"),
                InlineKeyboardButton(text="🔄 Regenerate", callback_data="regenthread"),
            ],
            [
                InlineKeyboardButton(text="❌ Cancel", callback_data="cancelthread"),
            ],
        ])
        await message.answer(
            f"🧵 Thread ({len(parts)} parts):\n\n{preview}",
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🧵 Post to Threads", callback_data="postthread"),
                InlineKeyboardButton(text="🔄 Regenerate", callback_data="regenthread"),
            ],
            [
                InlineKeyboardButton(text="❌ Cancel", callback_data="cancelthread"),
            ],
        ])
        await message.answer(
            f"🧵 Single post ({len(parts[0])} chars):\n\n{parts[0]}",
            reply_markup=keyboard
        )


pending_threads_content = {}


@router.callback_query(F.data == "postthread")
async def cb_post_thread(callback: CallbackQuery):
    """Publish thread to Threads."""
    pending = pending_threads_content.pop(callback.from_user.id, None)
    if not pending:
        await callback.answer("No pending thread", show_alert=True)
        return

    threads_data = await get_threads_token_or_env(pool)
    if not threads_data:
        await callback.answer("Threads not connected!", show_alert=True)
        return

    await callback.answer("Publishing to Threads...")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        parts = pending["parts"]
        if len(parts) == 1:
            result = await post_to_threads(
                threads_data["access_token"],
                threads_data["user_id"],
                parts[0],
            )
        else:
            result = await post_thread_chain(
                threads_data["access_token"],
                threads_data["user_id"],
                parts,
            )

        if result.get("success"):
            count = result.get("count", 1)
            await callback.message.reply(f"🧵 Posted to Threads! ({count} parts)")
        else:
            await callback.message.reply(f"❌ Threads error: {result}")
    except Exception as e:
        logger.error(f"Threads post error: {e}")
        await callback.message.reply(f"❌ Threads error: {e}")


@router.callback_query(F.data == "regenthread")
async def cb_regen_thread(callback: CallbackQuery):
    """Regenerate Threads content."""
    pending = pending_threads_content.get(callback.from_user.id)
    if not pending:
        await callback.answer("Nothing to regenerate", show_alert=True)
        return

    await callback.answer("Regenerating...")
    await callback.message.edit_reply_markup(reply_markup=None)

    # Use first part as topic hint
    topic = pending["parts"][0][:100]
    content = await generate_threads_content(topic)
    parts = content.get("parts", [])
    fmt = content.get("format", "single")

    if not parts:
        await callback.message.reply("❌ Failed to regenerate.")
        return

    pending_threads_content[callback.from_user.id] = {"parts": parts, "format": fmt}

    if fmt == "thread" and len(parts) > 1:
        preview = "\n\n---\n\n".join(parts)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🧵 Post Thread ({len(parts)} parts)", callback_data="postthread"),
                InlineKeyboardButton(text="🔄 Regenerate", callback_data="regenthread"),
            ],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancelthread")],
        ])
        await callback.message.reply(f"🧵 Thread ({len(parts)} parts):\n\n{preview}", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🧵 Post to Threads", callback_data="postthread"),
                InlineKeyboardButton(text="🔄 Regenerate", callback_data="regenthread"),
            ],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancelthread")],
        ])
        await callback.message.reply(f"🧵 Single post:\n\n{parts[0]}", reply_markup=keyboard)


@router.callback_query(F.data == "cancelthread")
async def cb_cancel_thread(callback: CallbackQuery):
    pending_threads_content.pop(callback.from_user.id, None)
    await callback.answer("Cancelled")
    await callback.message.edit_reply_markup(reply_markup=None)


# Comment state storage
found_posts_cache = {}
pending_comments = {}


@router.callback_query(F.data.startswith("gencomment:"))
async def cb_gen_comment(callback: CallbackQuery):
    """Generate comment for a found post."""
    idx = int(callback.data.split(":")[1])
    posts = found_posts_cache.get(callback.from_user.id, [])
    if idx >= len(posts):
        await callback.answer("Post not found", show_alert=True)
        return

    post = posts[idx]
    await callback.answer("Generating comment...")
    await callback.message.edit_reply_markup(reply_markup=None)

    comment_text = await generate_comment(post["url"], post["summary"])
    if not comment_text:
        await callback.message.reply("❌ Could not generate comment.")
        return

    pending_comments[callback.from_user.id] = {
        "url": post["url"],
        "comment": comment_text,
        "summary": post["summary"],
    }

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Post Comment", callback_data="postcomment"),
            InlineKeyboardButton(text="🔄 Regenerate", callback_data="regencomment"),
        ],
        [
            InlineKeyboardButton(text="✏️ Edit", callback_data="editcomment"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancelcomment"),
        ],
    ])

    await callback.message.reply(
        f"💬 Comment for {post.get('author', 'Unknown')}:\n\n{comment_text}",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "postcomment")
async def cb_post_comment(callback: CallbackQuery):
    """Post the approved comment."""
    pending = pending_comments.pop(callback.from_user.id, None)
    if not pending:
        await callback.answer("No pending comment", show_alert=True)
        return

    await callback.answer("Comment noted!")
    await callback.message.edit_reply_markup(reply_markup=None)

    # For now, copy comment to clipboard — LinkedIn comment API needs post URN
    # which we can't easily extract from URL without scraping
    await callback.message.reply(
        f"✅ Comment ready! Copy and paste on LinkedIn:\n\n"
        f"💬 {pending['comment']}\n\n"
        f"🔗 {pending['url']}"
    )


@router.callback_query(F.data == "regencomment")
async def cb_regen_comment(callback: CallbackQuery):
    """Regenerate the comment."""
    pending = pending_comments.get(callback.from_user.id)
    if not pending:
        await callback.answer("No pending comment", show_alert=True)
        return

    await callback.answer("Regenerating...")
    await callback.message.edit_reply_markup(reply_markup=None)

    comment_text = await generate_comment(pending["url"], pending["summary"])
    if not comment_text:
        await callback.message.reply("❌ Could not regenerate.")
        return

    pending_comments[callback.from_user.id]["comment"] = comment_text

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Post Comment", callback_data="postcomment"),
            InlineKeyboardButton(text="🔄 Regenerate", callback_data="regencomment"),
        ],
        [
            InlineKeyboardButton(text="✏️ Edit", callback_data="editcomment"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancelcomment"),
        ],
    ])

    await callback.message.reply(
        f"💬 New comment:\n\n{comment_text}",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "editcomment")
async def cb_edit_comment(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply("✏️ Send me the edited comment text.")
    edit_states[callback.from_user.id] = "comment"


@router.callback_query(F.data == "cancelcomment")
async def cb_cancel_comment(callback: CallbackQuery):
    pending_comments.pop(callback.from_user.id, None)
    await callback.answer("Cancelled")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply("❌ Comment cancelled.")


@router.callback_query(F.data.startswith("skippost:"))
async def cb_skip_post(callback: CallbackQuery):
    await callback.answer("Skipped")
    await callback.message.edit_reply_markup(reply_markup=None)


async def send_approval(chat_id: int, post_id: int, generated: dict):
    """Send post for approval with inline buttons and meme images."""
    post_text = generated["post_text"]
    meme = generated.get("meme", {})

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Post + Meme", callback_data=f"approve:{post_id}"),
            InlineKeyboardButton(text="📝 Post Text Only", callback_data=f"approvetext:{post_id}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Regenerate", callback_data=f"regen:{post_id}"),
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{post_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Reject", callback_data=f"reject:{post_id}"),
        ],
    ])

    # Send the post text
    await bot.send_message(
        chat_id,
        f"📝 *Draft #{post_id}*\n\n{post_text}",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

    # Send visual based on type
    if meme and meme.get("source") == "imgflip" and meme.get("image_url"):
        try:
            photo = URLInputFile(meme["image_url"])
            await bot.send_photo(
                chat_id,
                photo=photo,
                caption=f"😂 {meme.get('template', 'Meme')}"
            )
        except Exception as e:
            logger.error(f"Failed to send meme image: {e}")
            await bot.send_message(
                chat_id,
                f"😂 Meme: {meme.get('template', '')}\n"
                f"Top: {meme.get('text0', '')}\n"
                f"Bottom: {meme.get('text1', '')}\n"
                f"🔗 {meme.get('image_url', '')}"
            )

    elif meme and meme.get("source") == "unsplash" and meme.get("image_url"):
        try:
            photo = URLInputFile(meme["image_url"])
            photographer = meme.get("photographer", "Unknown")
            await bot.send_photo(
                chat_id,
                photo=photo,
                caption=f"📷 Photo by {photographer} on Unsplash"
            )
        except Exception as e:
            logger.error(f"Failed to send Unsplash photo: {e}")
            await bot.send_message(chat_id, f"📷 Photo: {meme.get('image_url', '')}")

    elif meme and meme.get("source") == "none":
        await bot.send_message(chat_id, "📝 Visual: text-only post (no image)")

    elif meme and meme.get("source") == "claude_suggestion":
        await bot.send_message(
            chat_id,
            f"😂 Meme suggestion:\n"
            f"Template: {meme.get('template', '')}\n"
            f"Top: {meme.get('text0', '')}\n"
            f"Bottom: {meme.get('text1', '')}"
        )

    # Show fact-check results
    fact_check = generated.get("fact_check", {})
    if fact_check and fact_check.get("status") == "issues_found":
        issues = fact_check.get("issues", [])
        issue_lines = []
        for issue in issues:
            verdict = issue.get("verdict", "?")
            emoji = "✅" if verdict == "verified" else "⚠️" if verdict == "unverified" else "❌"
            issue_lines.append(f"{emoji} {issue.get('claim', '?')}\n   → {issue.get('note', '')}")
        
        suggestion = fact_check.get("suggestion", "")
        await bot.send_message(
            chat_id,
            f"🔍 *Fact Check:*\n\n"
            + "\n\n".join(issue_lines)
            + (f"\n\n💡 {suggestion}" if suggestion else ""),
            parse_mode="Markdown"
        )
    elif fact_check and fact_check.get("status") == "clean":
        await bot.send_message(chat_id, "✅ Fact check: all claims look clean")


# ==================== CALLBACK HANDLERS ====================

@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    post_data = await get_post(pool, post_id)
    if not post_data:
        await callback.answer("Post not found", show_alert=True)
        return

    token_data = await get_linkedin_token(pool)
    if not token_data:
        await callback.answer("LinkedIn not connected! Use /connect", show_alert=True)
        return

    await callback.answer("Posting to LinkedIn...")
    await callback.message.edit_reply_markup(reply_markup=None)

    # Use URN from /connect (database), log for debugging
    person_urn = token_data["person_urn"]
    logger.info(f"Posting with person_urn from DB: {person_urn}")
    await callback.message.reply(f"🔍 Trying URN: `{person_urn}`", parse_mode="Markdown")

    # Extract image URL if available (imgflip meme or attached photo)
    image_url = None
    if post_data.get("meme_suggestion"):
        try:
            meme_data = json.loads(post_data["meme_suggestion"]) if isinstance(post_data["meme_suggestion"], str) else post_data["meme_suggestion"]
            if meme_data and meme_data.get("source") in ("imgflip", "telegram_photo", "unsplash"):
                image_url = meme_data.get("image_url")
        except Exception:
            pass

    result = await post_to_linkedin(
        token_data["access_token"],
        person_urn,
        post_data["post_text"],
        image_url=image_url,
    )

    if result["success"]:
        await update_post_status(pool, post_id, "posted", result["post_id"])
        threads_data = await get_threads_token_or_env(pool)
        if threads_data:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧵 Also post to Threads", callback_data=f"threads:{post_id}")],
            ])
            await callback.message.reply("✅ Posted to LinkedIn!", reply_markup=keyboard)
        else:
            await callback.message.reply("✅ Posted to LinkedIn!")
    else:
        await update_post_status(pool, post_id, "draft")
        await callback.message.reply(f"❌ LinkedIn error: {result['error']}")


@router.callback_query(F.data.startswith("approvetext:"))
async def cb_approve_text_only(callback: CallbackQuery):
    """Post text only, no meme image."""
    post_id = int(callback.data.split(":")[1])
    post_data = await get_post(pool, post_id)
    if not post_data:
        await callback.answer("Post not found", show_alert=True)
        return

    token_data = await get_linkedin_token(pool)
    if not token_data:
        await callback.answer("LinkedIn not connected! Use /connect", show_alert=True)
        return

    await callback.answer("Posting text only...")
    await callback.message.edit_reply_markup(reply_markup=None)

    person_urn = token_data["person_urn"]

    result = await post_to_linkedin(
        token_data["access_token"],
        person_urn,
        post_data["post_text"],
        image_url=None,
    )

    if result["success"]:
        await update_post_status(pool, post_id, "posted", result["post_id"])
        threads_data = await get_threads_token_or_env(pool)
        if threads_data:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧵 Also post to Threads", callback_data=f"threads:{post_id}")],
            ])
            await callback.message.reply("✅ Posted to LinkedIn (text only)!", reply_markup=keyboard)
        else:
            await callback.message.reply("✅ Posted to LinkedIn (text only)!")
    else:
        await update_post_status(pool, post_id, "draft")
        await callback.message.reply(f"❌ LinkedIn error: {result['error']}")


@router.callback_query(F.data.startswith("threads:"))
async def cb_post_to_threads(callback: CallbackQuery):
    """Generate Threads-native content from LinkedIn post."""
    post_id = int(callback.data.split(":")[1])
    post_data = await get_post(pool, post_id)
    if not post_data:
        await callback.answer("Post not found", show_alert=True)
        return

    threads_data = await get_threads_token_or_env(pool)
    if not threads_data:
        await callback.answer("Threads not connected! Use /threads", show_alert=True)
        return

    await callback.answer("Generating Threads version...")
    await callback.message.edit_reply_markup(reply_markup=None)

    # Generate full thread from LinkedIn post topic
    content = await generate_threads_content(post_data["post_text"][:200])
    parts = content.get("parts", [])
    fmt = content.get("format", "single")

    if not parts:
        await callback.message.reply("❌ Failed to generate Threads content.")
        return

    # Store for approval
    pending_threads[callback.from_user.id] = {
        "post_id": post_id,
        "parts": parts,
        "format": fmt,
    }

    if fmt == "thread" and len(parts) > 1:
        preview = "\n\n---\n\n".join(parts)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🧵 Post Thread ({len(parts)} parts)", callback_data=f"threadsconfirm:{post_id}"),
                InlineKeyboardButton(text="❌ Skip", callback_data="cancelthread"),
            ],
        ])
        await callback.message.reply(f"🧵 Thread ({len(parts)} parts):\n\n{preview}", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🧵 Post to Threads", callback_data=f"threadsconfirm:{post_id}"),
                InlineKeyboardButton(text="❌ Skip", callback_data="cancelthread"),
            ],
        ])
        await callback.message.reply(f"🧵 Single post ({len(parts[0])} chars):\n\n{parts[0]}", reply_markup=keyboard)


# Threads state
pending_threads = {}


@router.callback_query(F.data.startswith("threadsconfirm:"))
async def cb_confirm_threads(callback: CallbackQuery):
    """Publish to Threads — single post or thread chain."""
    pending = pending_threads.pop(callback.from_user.id, None)
    if not pending:
        await callback.answer("No pending Threads post", show_alert=True)
        return

    threads_data = await get_threads_token_or_env(pool)
    if not threads_data:
        await callback.answer("Threads not connected!", show_alert=True)
        return

    await callback.answer("Posting to Threads...")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        parts = pending.get("parts", [pending.get("text", "")])
        if len(parts) == 1:
            result = await post_to_threads(
                threads_data["access_token"],
                threads_data["user_id"],
                parts[0],
            )
        else:
            result = await post_thread_chain(
                threads_data["access_token"],
                threads_data["user_id"],
                parts,
            )
        if result.get("success"):
            count = result.get("count", 1)
            post_id = pending.get("post_id")
            threads_pid = result.get("post_id") or (result.get("post_ids", [None])[0])
            if post_id and threads_pid:
                from database import save_threads_post_id
                await save_threads_post_id(pool, post_id, str(threads_pid))
            await callback.message.reply(f"🧵 Posted to Threads! ({count} parts)")
        else:
            await callback.message.reply(f"❌ Threads error: {result}")
    except Exception as e:
        logger.error(f"Threads post error: {e}")
        await callback.message.reply(f"❌ Threads error: {e}")


@router.callback_query(F.data.startswith("regen:"))
async def cb_regenerate(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await callback.answer("Regenerating...")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply("🔄 Regenerating post...")

    post_data = await get_post(pool, post_id)
    if not post_data:
        return

    try:
        generated = await generate_post_from_topic(post_data["post_text"][:100], pool=pool)
        await update_post_text(pool, post_id, generated["post_text"], generated.get("meme"))
        await send_approval(callback.message.chat.id, post_id, generated)
    except Exception as e:
        await callback.message.reply(f"❌ Error: {e}")


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await callback.answer()
    await callback.message.reply(
        f"✏️ Send me the edited text for post #{post_id}.\n"
        f"Reply to this message with the full updated post text."
    )
    # Store edit state — next message from admin will be treated as edit
    edit_states[callback.from_user.id] = post_id


@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(callback: CallbackQuery):
    post_id = int(callback.data.split(":")[1])
    await update_post_status(pool, post_id, "rejected")
    await callback.answer("Post rejected")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.reply(
        f"❌ Post #{post_id} rejected.\n"
        f"💬 Why? Send a short reason so I learn (or /skip to skip)."
    )
    reject_states[callback.from_user.id] = post_id


# State tracking
edit_states = {}
reject_states = {}

@router.message(Command("skip"))
async def cmd_skip(message: Message):
    if message.from_user.id in reject_states:
        reject_states.pop(message.from_user.id)
        await message.answer("⏭ Skipped. No feedback saved.")

@router.message(Command("context"))
async def cmd_context(message: Message):
    """Add a context note that both LinkedIn bot and digest bot will use."""
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    text = message.text.replace("/context", "", 1).strip()
    if not text:
        await message.answer("Usage: /context <fact or update about your life/work>")
        return
    
    # Save to LinkedIn bot DB
    from database import add_user_context
    await add_user_context(pool, text)
    
    # Also save to digest bot DB (life_context table)
    import os
    digest_db_url = os.getenv("DIGEST_DATABASE_URL", "")
    if digest_db_url:
        try:
            import asyncpg
            conn = await asyncpg.connect(digest_db_url, timeout=10)
            await conn.execute(
                "INSERT INTO life_context (context, updated_at) VALUES ($1, NOW())",
                text
            )
            await conn.close()
            await message.answer(f"✅ Context saved to both bots: {text}")
        except Exception as e:
            logger.error(f"Failed to save to digest DB: {e}")
            await message.answer(f"✅ Saved to LinkedIn bot. ⚠️ Digest DB error: {e}")
    else:
        await message.answer(f"✅ Context saved: {text}")

@router.message(F.text & ~F.text.startswith("/"))
async def handle_free_text(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    # Handle reject reason
    if message.from_user.id in reject_states:
        post_id = reject_states.pop(message.from_user.id)
        from database import set_reject_reason
        await set_reject_reason(pool, post_id, message.text)
        await message.answer(f"📝 Feedback saved for post #{post_id}. I'll avoid this in future posts.")
        return

    # Handle edit
    if message.from_user.id in edit_states:
        state = edit_states.pop(message.from_user.id)
        new_text = message.text

        # Comment edit
        if state == "comment":
            if message.from_user.id in pending_comments:
                pending_comments[message.from_user.id]["comment"] = new_text
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Post Comment", callback_data="postcomment"),
                        InlineKeyboardButton(text="❌ Cancel", callback_data="cancelcomment"),
                    ],
                ])
                await message.answer(
                    f"✅ Comment updated:\n\n💬 {new_text}",
                    reply_markup=keyboard
                )
            return

        # Post edit
        post_id = state
        await update_post_text(pool, post_id, new_text)
        generated = {"post_text": new_text, "meme": None}
        await message.answer("✅ Post updated! Here's the new version:")
        await send_approval(message.chat.id, post_id, generated)
        return


# ==================== SCHEDULER ====================

async def scheduled_post_generation():
    """Run on schedule — generate post from life context for approval."""
    while True:
        now = datetime.now(timezone.utc)
        # Tashkent is UTC+5
        tashkent_hour = (now.hour + 5) % 24
        tashkent_weekday = now.weekday()

        if tashkent_weekday in POST_DAYS and tashkent_hour == POST_HOUR:
            logger.info("Scheduled post generation triggered")
            try:
                from post_generator import build_learning_context
                from digest_reader import get_digest_context
                
                life_context = await get_digest_context()
                learning = await build_learning_context(pool)
                combined = f"{life_context}\n\n{learning}".strip()
                
                if combined:
                    generated = await generate_post_from_digest(combined, pool=pool)
                    post_id = await save_post(pool, [], generated["post_text"], generated.get("meme"))
                    await send_approval(TELEGRAM_ADMIN_ID, post_id, generated)
                    logger.info(f"Scheduled post #{post_id} sent for approval")
                else:
                    logger.info("No context available for scheduled post")
            except Exception as e:
                logger.error(f"Scheduled generation error: {e}")
                await bot.send_message(TELEGRAM_ADMIN_ID, f"⚠️ Scheduled post error: {e}")

            # Wait 1 hour to avoid re-triggering
            await asyncio.sleep(3600)
        else:
            # Check every 10 minutes
            await asyncio.sleep(600)


async def scheduled_stats_collection():
    """Collect engagement stats from LinkedIn and Threads once daily."""
    while True:
        now = datetime.now(timezone.utc)
        tashkent_hour = (now.hour + 5) % 24

        # Run at 21:00 Tashkent time (evening, after engagement settles)
        if tashkent_hour == 21:
            logger.info("Running daily stats collection")
            try:
                li_token = await get_linkedin_token(pool)
                threads_data = await get_threads_token_or_env(pool)
                
                updated = await collect_all_stats(pool, li_token, threads_data)
                if updated:
                    logger.info(f"Stats updated for {updated} posts")
            except Exception as e:
                logger.error(f"Stats collection error: {e}")

            await asyncio.sleep(3600)
        else:
            await asyncio.sleep(600)


# ==================== WEB SERVER (OAuth callback) ====================

async def handle_linkedin_callback(request):
    """Handle LinkedIn OAuth callback."""
    code = request.query.get("code")
    error = request.query.get("error")

    if error:
        return web.Response(text=f"LinkedIn auth error: {error}", status=400)

    if not code:
        return web.Response(text="No code provided", status=400)

    try:
        token_data = await exchange_code(code)
        await save_linkedin_token(
            pool,
            token_data["access_token"],
            token_data["expires_at"],
            token_data["person_urn"],
        )
        await bot.send_message(
            TELEGRAM_ADMIN_ID,
            f"✅ LinkedIn connected!\nPerson URN: `{token_data['person_urn']}`\n"
            f"Token expires: {token_data['expires_at'].strftime('%Y-%m-%d')}",
            parse_mode="Markdown"
        )
        return web.Response(
            text="<h1>✅ LinkedIn Connected!</h1><p>Go back to Telegram.</p>",
            content_type="text/html"
        )
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        return web.Response(text=f"Error: {e}", status=500)


async def handle_threads_callback(request):
    """Handle Threads OAuth callback."""
    logger.info(f"Threads callback hit! Query: {dict(request.query)}")
    code = request.query.get("code")
    error = request.query.get("error")
    error_reason = request.query.get("error_reason", "")
    error_description = request.query.get("error_description", "")

    if error:
        logger.error(f"Threads auth error: {error} - {error_reason} - {error_description}")
        return web.Response(text=f"Threads auth error: {error} - {error_description}", status=400)

    if not code:
        return web.Response(text="No code provided", status=400)

    # Threads sometimes appends #_ to the code
    code = code.replace("#_", "").strip()

    try:
        token_data = await exchange_threads_code(code)
        await save_threads_token(
            pool,
            token_data["access_token"],
            token_data["user_id"],
            token_data.get("expires_in", 5184000),
        )
        await bot.send_message(
            TELEGRAM_ADMIN_ID,
            f"🧵 Threads connected!\nUser ID: `{token_data['user_id']}`",
            parse_mode="Markdown"
        )
        return web.Response(
            text="<h1>🧵 Threads Connected!</h1><p>Go back to Telegram.</p>",
            content_type="text/html"
        )
    except Exception as e:
        logger.error(f"Threads OAuth error: {e}")
        return web.Response(text=f"Error: {e}", status=500)


async def health_check(request):
    return web.Response(text="OK")


# ==================== MAIN ====================

async def main():
    global pool

    # Init database
    pool = await get_pool()
    await init_db(pool)
    logger.info("Database initialized")

    # Start web server for OAuth callback
    app = web.Application()
    app.router.add_get("/linkedin/callback", handle_linkedin_callback)
    app.router.add_get("/threads/callback", handle_threads_callback)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Web server started on :8080")

    # Start scheduler
    asyncio.create_task(scheduled_post_generation())
    asyncio.create_task(scheduled_stats_collection())
    logger.info(f"Scheduler started: days={POST_DAYS}, hour={POST_HOUR}:00 UZT")

    # Start bot
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
