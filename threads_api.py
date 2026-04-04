"""
Threads API module
- OAuth flow via Instagram authorization
- Create and publish text posts
- Adapt LinkedIn posts to Threads format (shorter, casual)
"""
import os
import json
import logging
import httpx
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

THREADS_APP_ID = os.getenv("THREADS_APP_ID", "1630209911587747")
THREADS_APP_SECRET = os.getenv("THREADS_APP_SECRET", "")
THREADS_REDIRECT_URI = os.getenv("THREADS_REDIRECT_URI", "https://linkedin-poster-production-5217.up.railway.app/threads/callback")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "")
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "")

GRAPH_API_BASE = "https://graph.threads.net"


def get_threads_auth_url() -> str:
    """Generate Threads OAuth authorization URL."""
    params = {
        "client_id": THREADS_APP_ID,
        "redirect_uri": THREADS_REDIRECT_URI,
        "scope": "threads_basic,threads_content_publish",
        "response_type": "code",
    }
    return f"https://www.threads.net/oauth/authorize?{urlencode(params)}"


async def exchange_threads_code(code: str) -> dict:
    """Exchange authorization code for access token."""
    async with httpx.AsyncClient(timeout=15) as client:
        # Step 1: Get short-lived token
        resp = await client.post(
            f"{GRAPH_API_BASE}/oauth/access_token",
            data={
                "client_id": THREADS_APP_ID,
                "client_secret": THREADS_APP_SECRET,
                "grant_type": "authorization_code",
                "redirect_uri": THREADS_REDIRECT_URI,
                "code": code,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        short_token = data["access_token"]
        user_id = data["user_id"]

        # Step 2: Exchange for long-lived token (60 days)
        resp2 = await client.get(
            f"{GRAPH_API_BASE}/access_token",
            params={
                "grant_type": "th_exchange_token",
                "client_secret": THREADS_APP_SECRET,
                "access_token": short_token,
            },
        )
        resp2.raise_for_status()
        long_data = resp2.json()

        return {
            "access_token": long_data["access_token"],
            "user_id": str(user_id),
            "expires_in": long_data.get("expires_in", 5184000),
        }


async def post_to_threads(access_token: str, user_id: str, text: str) -> dict:
    """
    Post text to Threads. Two-step process:
    1. Create media container
    2. Publish the container
    """
    async with httpx.AsyncClient(timeout=30) as client:
        # Step 1: Create media container
        resp = await client.post(
            f"{GRAPH_API_BASE}/v1.0/{user_id}/threads",
            params={
                "media_type": "TEXT",
                "text": text[:500],
                "access_token": access_token,
            },
        )
        resp.raise_for_status()
        container_id = resp.json()["id"]

        logger.info(f"Created Threads container: {container_id}")

        import asyncio
        await asyncio.sleep(3)

        # Step 2: Publish
        resp2 = await client.post(
            f"{GRAPH_API_BASE}/v1.0/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": access_token,
            },
        )
        resp2.raise_for_status()
        post_id = resp2.json()["id"]

        logger.info(f"Published to Threads: {post_id}")
        return {"success": True, "post_id": post_id}


async def post_reply_to_threads(access_token: str, user_id: str, text: str, reply_to_id: str) -> dict:
    """Post a reply to an existing Threads post (for thread chains)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GRAPH_API_BASE}/v1.0/{user_id}/threads",
            params={
                "media_type": "TEXT",
                "text": text[:500],
                "reply_to_id": reply_to_id,
                "access_token": access_token,
            },
        )
        resp.raise_for_status()
        container_id = resp.json()["id"]

        import asyncio
        await asyncio.sleep(3)

        resp2 = await client.post(
            f"{GRAPH_API_BASE}/v1.0/{user_id}/threads_publish",
            params={
                "creation_id": container_id,
                "access_token": access_token,
            },
        )
        resp2.raise_for_status()
        post_id = resp2.json()["id"]

        logger.info(f"Published reply to Threads: {post_id}")
        return {"success": True, "post_id": post_id}


async def post_thread_chain(access_token: str, user_id: str, parts: list) -> dict:
    """Post a multi-part thread: first post + replies."""
    if not parts:
        return {"success": False, "error": "No parts"}

    # Post first part
    result = await post_to_threads(access_token, user_id, parts[0])
    if not result.get("success"):
        return result

    import asyncio
    last_id = result["post_id"]
    posted_ids = [last_id]

    # Post replies
    for part in parts[1:]:
        await asyncio.sleep(2)
        try:
            reply = await post_reply_to_threads(access_token, user_id, part, last_id)
            if reply.get("success"):
                last_id = reply["post_id"]
                posted_ids.append(last_id)
            else:
                logger.error(f"Reply failed: {reply}")
                break
        except Exception as e:
            logger.error(f"Thread chain error: {e}")
            break

    return {"success": True, "post_ids": posted_ids, "count": len(posted_ids)}


async def generate_threads_content(topic: str) -> dict:
    """
    Generate Threads-native content. Claude decides format:
    - Single post (hot take, question)
    - Multi-part thread (story, tutorial, list)
    """
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "You write Threads posts for Robert — an AI automation engineer "
                                "who builds bots and systems for media companies in Central Asia.\n\n"
                                "Generate a Threads post or thread about this topic:\n"
                                f"{topic}\n\n"
                                "DECIDE THE FORMAT:\n"
                                "- If it's a hot take, opinion, or question → single post (max 480 chars)\n"
                                "- If it's a story, tutorial, breakdown, or list → multi-part thread (3-8 parts)\n\n"
                                "THREAD FORMAT RULES:\n"
                                "- Part 1: HOOK. Must make people stop scrolling. Curiosity gap or bold claim.\n"
                                "  End with something that makes them tap to read more.\n"
                                "- Parts 2-7: STORY/VALUE. Each part max 480 chars. Each must stand alone AND connect.\n"
                                "  Do NOT number the parts (no 1/6, 2/6 etc). Let each part flow naturally.\n"
                                "- Last part: CTA or punchline. Question, takeaway, or call to action.\n\n"
                                "STYLE:\n"
                                "- Casual, direct, like texting a smart friend\n"
                                "- No hashtags in thread parts (only last part, max 2)\n"
                                "- No emojis overload, no markdown\n"
                                "- Short sentences. Line breaks between thoughts.\n"
                                "- English only\n"
                                "- Real experiences > generic advice\n\n"
                                "Return ONLY a JSON object:\n"
                                '{"format": "single" or "thread", "parts": ["part1 text", "part2 text", ...]}\n\n'
                                "For single posts, parts array has 1 item.\n"
                                "Return ONLY valid JSON."
                            ),
                        }
                    ],
                },
            )
            resp.raise_for_status()
            raw = resp.json()["content"][0]["text"].strip()
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(cleaned[start:end])
                # Ensure all parts under 500 chars
                result["parts"] = [p[:497] + "..." if len(p) > 500 else p for p in result["parts"]]
                return result

            return {"format": "single", "parts": [topic[:480]]}

    except Exception as e:
        logger.error(f"Threads generation error: {e}")
        return {"format": "single", "parts": [topic[:480]]}


async def adapt_post_for_threads(linkedin_text: str) -> str:
    """
    Adapt a LinkedIn post to Threads format using Claude.
    - Shorter (max 500 chars)
    - More casual
    - Question or hook at the end
    """
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Adapt this LinkedIn post for Threads. Rules:\n"
                                "- MAX 480 characters (hard limit)\n"
                                "- More casual and punchy than LinkedIn\n"
                                "- Keep the core insight but cut the fluff\n"
                                "- End with a question or bold statement\n"
                                "- No hashtags, no markdown, no emojis overload\n"
                                "- Sound like a real person talking, not a brand\n"
                                "- English only\n\n"
                                "Write ONLY the Threads post text. Nothing else.\n\n"
                                f"LinkedIn post:\n{linkedin_text}"
                            ),
                        }
                    ],
                },
            )
            resp.raise_for_status()
            threads_text = resp.json()["content"][0]["text"].strip()
            # Ensure under 500 chars
            if len(threads_text) > 500:
                threads_text = threads_text[:497] + "..."
            return threads_text
    except Exception as e:
        logger.error(f"Threads adaptation error: {e}")
        # Fallback: just truncate
        if len(linkedin_text) > 500:
            return linkedin_text[:497] + "..."
        return linkedin_text
