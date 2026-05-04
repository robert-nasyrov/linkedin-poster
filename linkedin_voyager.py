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
import re
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _numeric_id(post_id: str) -> str:
    """Extract bare numeric id from any URN form ('urn:li:share:NNN' → 'NNN')."""
    sid = (post_id or "").strip()
    if ":" in sid:
        sid = sid.rsplit(":", 1)[-1]
    return sid


def _urn_candidates(post_id: str) -> list[str]:
    """A post's numeric id can resolve under multiple URN namespaces.
    Try them in order — Voyager will 404 on the wrong one, 200 on the right."""
    nid = _numeric_id(post_id)
    return [
        f"urn:li:activity:{nid}",
        f"urn:li:share:{nid}",
        f"urn:li:ugcPost:{nid}",
    ]


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


_ACTIVITY_RE = re.compile(r"urn[:%]li[:%]activity[:%](\d+)")

# LinkedIn ships engagement counts under several field name conventions
# depending on which response shape the frontend expected. Match any of them
# (in JSON) and we'll classify by name afterwards. Allow both " and &quot;
# so we can read counts even when the JSON was HTML-escaped inside <code>.
_COUNT_RE = re.compile(
    r'(?:"|&quot;)([a-zA-Z]+)(?:"|&quot;)\s*:\s*(\d+)'
)

_LIKE_KEYS = (
    "numLikes", "reactionCount", "reactionsCount", "numReactions",
    "likeCount", "likesCount",
)
_COMMENT_KEYS = (
    "numComments", "commentCount", "commentsCount",
)
_SHARE_KEYS = (
    "numShares", "shareCount", "sharesCount",
    "numReshares", "reshareCount", "repostCount", "repostsCount",
)
_VIEW_KEYS = (
    "numImpressions", "impressionCount",
    "numViews", "viewCount", "viewsCount",
)


def _classify_counts(html: str) -> tuple[dict | None, dict]:
    """Walk every numeric JSON field in the HTML, take the MAX value seen for
    each known synonym (LinkedIn embeds the same data multiple times in its
    initial-state blobs — feed card, post detail, analytics — and we want the
    canonical / largest one). Returns (counts_or_none, all_seen_for_debug)."""
    seen_max: dict[str, int] = {}
    for name, val in _COUNT_RE.findall(html):
        try:
            v = int(val)
        except ValueError:
            continue
        if v > seen_max.get(name, -1):
            seen_max[name] = v

    def pick(keys):
        for k in keys:
            if k in seen_max:
                return seen_max[k]
        return 0

    likes = pick(_LIKE_KEYS)
    comments = pick(_COMMENT_KEYS)
    shares = pick(_SHARE_KEYS)
    views = pick(_VIEW_KEYS)

    if likes or comments or shares or views:
        return ({
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "views": views,
        }, seen_max)
    return (None, seen_max)


def _page_headers() -> dict:
    return {
        "user-agent": _BROWSER_UA,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "referer": "https://www.linkedin.com/feed/",
    }


async def fetch_post_page(li_at: str, jsessionid: str, share_id: str
                          ) -> tuple[str | None, dict | None, dict]:
    """Fetch the public post page once and pull EVERYTHING we need from it:
    activity URN + engagement counts. The HTML embeds initial state JSON that
    LinkedIn's own frontend reads, so the data is reliably present.

    Returns (activity_urn, engagement_dict, debug). On 401/403 returns
    (None, {"_auth_error": True}, debug).
    """
    nid = _numeric_id(share_id)
    debug: dict = {"tried": []}

    async with httpx.AsyncClient() as client:
        for prefix in ("urn:li:share", "urn:li:ugcPost", "urn:li:activity"):
            url = f"https://www.linkedin.com/feed/update/{prefix}:{nid}/"
            try:
                resp = await client.get(
                    url,
                    headers=_page_headers(),
                    cookies=_build_cookies(li_at, jsessionid),
                    timeout=25,
                    follow_redirects=True,
                )
            except Exception as e:
                debug["tried"].append({"url": url, "error": str(e)})
                continue

            debug["tried"].append({
                "url": url, "status": resp.status_code,
                "final_url": str(resp.url), "body_len": len(resp.text or ""),
            })

            if resp.status_code in (401, 403):
                logger.warning(f"Page auth failed ({resp.status_code}) for {prefix}:{nid}")
                return (None, {"_auth_error": True}, debug)
            if resp.status_code != 200 or not resp.text:
                continue

            html = resp.text

            activity = None
            m = _ACTIVITY_RE.search(str(resp.url))
            if m:
                activity = m.group(1)
            if not activity:
                m = _ACTIVITY_RE.search(html)
                if m:
                    activity = m.group(1)

            engagement, seen = _classify_counts(html)
            if engagement is None and seen:
                # We saw count-shaped fields but none matched our synonym list —
                # log what we DID see so we can extend _LIKE_KEYS / etc.
                # Filter to only fields with words that hint at engagement.
                hint = {k: v for k, v in seen.items()
                        if any(t in k.lower() for t in
                               ("like", "comment", "share", "react", "view",
                                "impress", "repost", "reshare"))}
                if hint:
                    logger.info(f"Engagement-shaped fields in page (unmatched): {hint}")
                else:
                    logger.info(
                        f"No engagement-like fields found. Top numeric fields "
                        f"seen: {dict(list(seen.items())[:15])}"
                    )

            if activity or engagement:
                logger.info(
                    f"Page hit on {prefix}:{nid} — activity={activity}, "
                    f"engagement={engagement}"
                )
                return (activity, engagement, debug)

    return (None, None, debug)


async def resolve_activity_urn(li_at: str, jsessionid: str,
                               share_id: str) -> str | None:
    """Backwards-compat shim — only the URN."""
    activity, _, _ = await fetch_post_page(li_at, jsessionid, share_id)
    return activity


def _parse_counts(data: dict) -> dict | None:
    """Pull engagement counts out of Voyager's normalized response shape.
    Returns None if no counts could be located."""
    # Direct: socialDetail at top level
    sd = data.get("socialDetail")
    if isinstance(sd, dict):
        counts = sd.get("totalSocialActivityCounts") or {}
        if counts:
            return {
                "likes": int(counts.get("numLikes", 0) or 0),
                "comments": int(counts.get("numComments", 0) or 0),
                "shares": int(counts.get("numShares", 0) or 0),
                "views": int(counts.get("numImpressions", 0) or 0),
            }

    # Normalized: counts may live in `included` array under the right $type
    for el in data.get("included", []) or []:
        t = el.get("$type", "") or ""
        if "SocialActivityCounts" in t:
            return {
                "likes": int(el.get("numLikes", 0) or 0),
                "comments": int(el.get("numComments", 0) or 0),
                "shares": int(el.get("numShares", 0) or 0),
                "views": int(el.get("numImpressions", 0) or 0),
            }

    return None


async def fetch_engagement(li_at: str, jsessionid: str, post_id: str) -> dict | None:
    """Get engagement counts. We first try the HTML page (most reliable —
    LinkedIn's own frontend reads from it). Voyager API is kept as a probe
    in case the page-embedded state ever stops including counts.
    """
    # Primary: HTML page parse
    _, engagement, _ = await fetch_post_page(li_at, jsessionid, post_id)
    if engagement:
        return engagement

    # Fallback: Voyager updateV2 (may 404 on modern LinkedIn but cheap to try)
    async with httpx.AsyncClient() as client:
        for urn in _urn_candidates(post_id):
            encoded = quote(urn, safe="")
            url = f"https://www.linkedin.com/voyager/api/feed/updateV2/{encoded}"
            try:
                resp = await _voyager_get(client, url, li_at, jsessionid)
            except Exception:
                continue
            if resp.status_code in (401, 403):
                return {"_auth_error": True}
            if resp.status_code == 200:
                try:
                    parsed = _parse_counts(resp.json())
                except Exception:
                    continue
                if parsed:
                    return parsed
    return None


async def fetch_comments(li_at: str, jsessionid: str, post_id: str,
                         limit: int = 20) -> list[dict]:
    """Try each URN namespace for the Voyager comments endpoint.
    Returns [] on any error."""
    async with httpx.AsyncClient() as client:
        for urn in _urn_candidates(post_id):
            encoded = quote(urn, safe="")
            url = (
                "https://www.linkedin.com/voyager/api/feed/comments"
                f"?numComments={int(limit)}&q=updateV2&start=0&updateId={encoded}"
            )
            try:
                resp = await _voyager_get(client, url, li_at, jsessionid)
            except Exception as e:
                logger.error(f"Voyager comments transport error for {urn}: {e}")
                continue
            if resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue
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
            if out:
                return out
        return []


async def jitter() -> None:
    """Random 3-8s pause between requests. Mimics a person scrolling, not a script."""
    await asyncio.sleep(random.uniform(3.0, 8.0))
