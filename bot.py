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
)
from digest_reader import fetch_recent_digests, fetch_digests_for_post
from post_generator import generate_post_from_digest, generate_post_from_topic
from linkedin_api import (
    get_auth_url, exchange_code, post_to_linkedin, check_token_valid,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

pool = None


# ==================== TELEGRAM COMMANDS ====================

@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    await message.answer(
        "🚀 *LinkedIn Auto-Poster Bot*\n\n"
        "Commands:\n"
        "/generate — Generate post from recent digests\n"
        "/write <topic> — Write post from your thought\n"
        "/post <text> — Post ready text directly (no AI)\n"
        "/connect — Connect LinkedIn account\n"
        "/status — Check bot & token status\n"
        "/fetch — Fetch latest digests now\n",
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


@router.message(Command("status"))
async def cmd_status(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    token_data = await get_linkedin_token(pool)
    if token_data:
        valid = await check_token_valid(token_data["access_token"])
        expires = token_data["expires_at"].strftime("%Y-%m-%d")
        status = "✅ Valid" if valid else "❌ Expired"
        await message.answer(
            f"*Bot Status*\n\n"
            f"LinkedIn: {status}\n"
            f"Token expires: {expires}\n"
            f"Person URN: `{token_data['person_urn']}`\n"
            f"Schedule: days {POST_DAYS}, hour {POST_HOUR}:00 UZT",
            parse_mode="Markdown"
        )
    else:
        await message.answer("LinkedIn: ❌ Not connected\nUse /connect to set up")


@router.message(Command("fetch"))
async def cmd_fetch(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    await message.answer("📡 Fetching digests...")
    msgs = await fetch_recent_digests(pool, hours=48)
    await message.answer(f"✅ Fetched {len(msgs)} new digest messages")


@router.message(Command("generate"))
async def cmd_generate(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return

    await message.answer("📡 Fetching latest digests...")
    await fetch_recent_digests(pool, hours=48)

    result = await fetch_digests_for_post(pool)
    if not result:
        await message.answer("❌ No unprocessed digests found. Try /fetch first or /write <topic>")
        return

    digest_text, digest_ids = result
    await message.answer(f"🧠 Analyzing {len(digest_ids)} digests and generating post...")

    try:
        generated = await generate_post_from_digest(digest_text)
        post_id = await save_post(pool, digest_ids, generated["post_text"], generated["meme"])
        await mark_digests_processed(pool, digest_ids)
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
        generated = await generate_post_from_topic(topic)
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

    # Send meme image from Imgflip
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
                f"😂 *Meme:* {meme.get('template', '')}\n"
                f"Top: {meme.get('text0', '')}\n"
                f"Bottom: {meme.get('text1', '')}\n"
                f"🔗 {meme.get('image_url', '')}",
                parse_mode="Markdown"
            )

    # Fallback: text-only meme suggestion
    elif meme and meme.get("source") == "claude_suggestion":
        await bot.send_message(
            chat_id,
            f"😂 *Meme suggestion:*\n"
            f"Template: `{meme.get('template', '')}`\n"
            f"Top: {meme.get('text0', '')}\n"
            f"Bottom: {meme.get('text1', '')}",
            parse_mode="Markdown"
        )


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
            if meme_data and meme_data.get("source") in ("imgflip", "telegram_photo"):
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
        await callback.message.reply("✅ Posted to LinkedIn (text only)!")
    else:
        await update_post_status(pool, post_id, "draft")
        await callback.message.reply(f"❌ LinkedIn error: {result['error']}")


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
        generated = await generate_post_from_topic(post_data["post_text"][:100])
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
    await callback.message.reply("❌ Post rejected and archived.")


# Edit state tracking
edit_states = {}

@router.message(F.text & ~F.text.startswith("/"))
async def handle_edit_text(message: Message):
    if message.from_user.id != TELEGRAM_ADMIN_ID:
        return
    if message.from_user.id not in edit_states:
        return

    post_id = edit_states.pop(message.from_user.id)
    new_text = message.text

    await update_post_text(pool, post_id, new_text)
    generated = {"post_text": new_text, "meme": None}
    await message.answer("✅ Post updated! Here's the new version:")
    await send_approval(message.chat.id, post_id, generated)


# ==================== SCHEDULER ====================

async def scheduled_post_generation():
    """Run on schedule — fetch digests and generate post for approval."""
    while True:
        now = datetime.now(timezone.utc)
        # Tashkent is UTC+5
        tashkent_hour = (now.hour + 5) % 24
        tashkent_weekday = now.weekday()

        if tashkent_weekday in POST_DAYS and tashkent_hour == POST_HOUR:
            logger.info("Scheduled post generation triggered")
            try:
                await fetch_recent_digests(pool, hours=48)
                result = await fetch_digests_for_post(pool)
                if result:
                    digest_text, digest_ids = result
                    generated = await generate_post_from_digest(digest_text)
                    post_id = await save_post(pool, digest_ids, generated["post_text"], generated["meme"])
                    await mark_digests_processed(pool, digest_ids)
                    await send_approval(TELEGRAM_ADMIN_ID, post_id, generated)
                    logger.info(f"Scheduled post #{post_id} sent for approval")
            except Exception as e:
                logger.error(f"Scheduled generation error: {e}")
                await bot.send_message(TELEGRAM_ADMIN_ID, f"⚠️ Scheduled post error: {e}")

            # Wait 1 hour to avoid re-triggering
            await asyncio.sleep(3600)
        else:
            # Check every 10 minutes
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
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Web server started on :8080")

    # Start scheduler
    asyncio.create_task(scheduled_post_generation())
    logger.info(f"Scheduler started: days={POST_DAYS}, hour={POST_HOUR}:00 UZT")

    # Start bot
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
