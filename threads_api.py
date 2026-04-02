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

GRAPH_API_BASE = "https://graph.threads.net"


def get_threads_auth_url() -> str:
    """Generate Threads OAuth authorization URL."""
    params = {
        "client_id": THREADS_APP_ID,
        "redirect_uri": THREADS_REDIRECT_URI,
        "scope": "threads_basic,threads_content_publish",
        "response_type": "code",
    }
    return f"https://threads.net/oauth/authorize?{urlencode(params)}"


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
                "text": text[:500],  # Threads limit
                "access_token": access_token,
            },
        )
        resp.raise_for_status()
        container_id = resp.json()["id"]

        logger.info(f"Created Threads container: {container_id}")

        # Wait a moment for processing
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
