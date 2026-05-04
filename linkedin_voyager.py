"""
LinkedIn Voyager client — unofficial API used by linkedin.com itself.

Authenticates with the user's own cookies (li_at + JSESSIONID) and queries
the same endpoints the browser hits when rendering a post. Used as a fallback
for engagement metrics on personal posts since the official `/v2/socialActions`
endpoint is closed to non-Marketing-Partner apps in 2024+.

Boundaries:
- ONLY for the user's own posts at low rate (≤ once per day per post).
- Mimics browser headers; no obvious automation markers.
- 3-8s random pause between requests.
- Detects 401/403 → caller should notify the user to re-paste cookies.

How Robert refreshes cookies (when 401 appears):
1. Open linkedin.com in Chrome, log in.
2. DevTools → Application → Cookies → https://www.linkedin.com
3. Copy values of `li_at` and `JSESSIONID`.
4. In the bot: /relink_li li_at=...; JSESSIONID="ajax:..."
"""
from __future__ import annotations

import asyncio
import logging
import random
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _activity_urn(post_id: str) -> str:
    """Map our stored post id (raw number or share URN) to an activity URN."""
    sid = (post_id or "").strip()
    if sid.startswith("urn:li:activity:"):
        return sid
    if sid.startswith("urn:li:share:"):
        sid = sid[len("urn:li:share:"):]
    elif sid.startswith("urn:li:ugcPost:"):
        sid = sid[len("urn:li:ugcPost:"):]
    elif ":" in sid:
        sid = sid.rsplit(":", 1)[-1]
    return f"urn:li:activity:{sid}"


def _build_headers(jsessionid: str) -> dict:
    """Browser-like headers for Voyager. csrf-token must equal JSESSIONID."""
    csrf = (jsessionid or "").strip().strip('"')
    return {
        "user-agent": _BROWSER_UA,
        "accept": "application/vnd.linkedin.normalized+json+2.1",
        "accept-language": "en-US,en;q=0.9",
        "x-restli-protocol-version": "2.0.0",
        "x-li-lang": "en_US",
        "x-li-track": (
            '{"clientVersion":"1.13.30000","mpVersion":"1.13.30000",'
            '"osName":"web","timezoneOffset":5,"timezone":"Asia/Tashkent",'
            '"deviceFormFactor":"DESKTOP","mpName":"voyager-web"}'
        ),
        "csrf-token": csrf,
        "referer": "https://www.linkedin.com/feed/",
    }


def _build_cookies(li_at: str, jsessionid: str) -> dict:
    js = (jsessionid or "").strip()
    if not js.startswith('"'):
        js = f'"{js}"'  # LinkedIn stores JSESSIONID quoted
    return {"li_at": li_at, "JSESSIONID": js}


async def _voyager_get(client: httpx.AsyncClient, url: str,
                       li_at: str, jsessionid: str) -> httpx.Response:
    return await client.get(
        url,
        headers=_build_headers(jsessionid),
        cookies=_build_cookies(li_at, jsessionid),
        timeout=20,
        follow_redirects=False,
    )


async def fetch_engagement(li_at: str, jsessionid: str, post_id: str) -> dict | None:
    """Return dict with likes/comments/shares/views from Voyager updateV2.
    Returns None on any error (auth, network, parsing)."""
    urn = _activity_urn(post_id)
    encoded = quote(urn, safe="")
    url = f"https://www.linkedin.com/voyager/api/feed/updateV2/{encoded}"

    try:
        async with httpx.AsyncClient() as client:
            resp = await _voyager_get(client, url, li_at, jsessionid)
            if resp.status_code in (401, 403):
                logger.warning(f"Voyager auth failed ({resp.status_code}) for {urn} — cookies need refresh")
                return {"_auth_error": True}
            if resp.status_code != 200:
                logger.warning(f"Voyager engagement {resp.status_code} for {urn}: {resp.text[:200]}")
                return None

            data = resp.json()
            counts = (
                data.get("socialDetail", {}).get("totalSocialActivityCounts", {})
                or {}
            )
            return {
                "likes": int(counts.get("numLikes", 0) or 0),
                "comments": int(counts.get("numComments", 0) or 0),
                "shares": int(counts.get("numShares", 0) or 0),
                # Views require a separate analytics endpoint that's only available
                # to authors. Punt on v1 — engagement signal is the load-bearing part.
                "views": int(counts.get("numImpressions", 0) or 0),
            }
    except Exception as e:
        logger.error(f"Voyager engagement error for {post_id}: {e}")
        return None


async def fetch_comments(li_at: str, jsessionid: str, post_id: str,
                         limit: int = 20) -> list[dict]:
    """Return list of {id, author, text} from the Voyager comments endpoint.
    Returns [] on any error."""
    urn = _activity_urn(post_id)
    encoded = quote(urn, safe="")
    url = (
        "https://www.linkedin.com/voyager/api/feed/comments"
        f"?numComments={int(limit)}&q=updateV2&start=0&updateId={encoded}"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await _voyager_get(client, url, li_at, jsessionid)
            if resp.status_code != 200:
                logger.warning(
                    f"Voyager comments {resp.status_code} for {urn}: {resp.text[:200]}"
                )
                return []

            data = resp.json()
            elements = data.get("elements") or data.get("included") or []
            out: list[dict] = []
            for el in elements:
                # Comments come as either direct elements or normalized in `included`
                if "commentV2" in el or "commenter" in el or "$type" not in el:
                    text_block = (
                        el.get("commentV2", {}).get("text", {}).get("text")
                        or el.get("commentary", {}).get("text")
                        or el.get("text", {}).get("text")
                        or ""
                    )
                    if not text_block:
                        continue
                    actor = (
                        el.get("commenter", {}).get("name", {}).get("text")
                        or el.get("commenterProfileUrl")
                        or "unknown"
                    )
                    cid = (
                        el.get("urn")
                        or el.get("entityUrn")
                        or el.get("commentUrn")
                        or f"{urn}:{len(out)}"
                    )
                    out.append({
                        "id": str(cid),
                        "author": str(actor),
                        "text": text_block,
                    })
            return out
    except Exception as e:
        logger.error(f"Voyager comments error for {post_id}: {e}")
        return []


async def jitter() -> None:
    """Random 3-8s pause between requests. Mimics a person scrolling, not a script."""
    await asyncio.sleep(random.uniform(3.0, 8.0))
