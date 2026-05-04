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


def _parse_cookie_string(raw: str) -> dict:
    """Parse 'name=value; name=value; ...' into a dict.
    Used to forward the user's full cookie jar — analytics page rejects
    requests with only li_at + JSESSIONID (needs liap, bcookie, lidc, bscookie too).
    """
    out: dict = {}
    if not raw:
        return out
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            name, value = part.split("=", 1)
            value = value.strip()
            # JSESSIONID is canonically stored quoted; preserve user's literal value
            # but strip any wrapping whitespace
            out[name.strip()] = value
    return out


def _build_cookies(li_at: str, jsessionid: str, raw: str = None) -> dict:
    """Build cookie jar. Prefer the full raw cookie string the user pasted;
    fall back to li_at + JSESSIONID for backward compat."""
    if raw:
        parsed = _parse_cookie_string(raw)
        if parsed.get("li_at"):
            return parsed
    js = (jsessionid or "").strip()
    if js and not js.startswith('"'):
        js = f'"{js}"'  # LinkedIn stores JSESSIONID quoted
    out = {"li_at": li_at}
    if js:
        out["JSESSIONID"] = js
    return out


async def _voyager_get(client: httpx.AsyncClient, url: str,
                       li_at: str, jsessionid: str,
                       raw_cookies: str = None) -> httpx.Response:
    return await client.get(
        url,
        headers=_build_headers(jsessionid),
        cookies=_build_cookies(li_at, jsessionid, raw_cookies),
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


async def _fetch_html(client: httpx.AsyncClient, url: str,
                      li_at: str, jsessionid: str,
                      raw_cookies: str = None) -> httpx.Response | None:
    try:
        return await client.get(
            url,
            headers=_page_headers(),
            cookies=_build_cookies(li_at, jsessionid, raw_cookies),
            timeout=25,
            follow_redirects=True,
        )
    except Exception as e:
        logger.error(f"HTML fetch transport error for {url}: {e}")
        return None


async def fetch_post_page(li_at: str, jsessionid: str, share_id: str,
                          raw_cookies: str = None
                          ) -> tuple[str | None, dict | None, dict]:
    """Get activity URN + engagement counts.

    Step 1: hit the public post detail page to resolve activity URN.
    Step 2: hit the AUTHOR-only analytics page for that activity — it has
    the actual likes/views/comments numbers embedded in HTML, while the
    public post page loads them via JS after render.

    Returns (activity_urn, engagement_dict, debug). On 401/403 returns
    (None, {"_auth_error": True}, debug).
    """
    nid = _numeric_id(share_id)
    debug: dict = {"tried": []}
    activity: str | None = None

    async with httpx.AsyncClient() as client:
        # Step 1: resolve activity URN from public post page
        for prefix in ("urn:li:share", "urn:li:ugcPost", "urn:li:activity"):
            url = f"https://www.linkedin.com/feed/update/{prefix}:{nid}/"
            resp = await _fetch_html(client, url, li_at, jsessionid, raw_cookies)
            if resp is None:
                continue

            debug["tried"].append({
                "url": url, "status": resp.status_code,
                "final_url": str(resp.url), "body_len": len(resp.text or ""),
            })

            if resp.status_code in (401, 403):
                return (None, {"_auth_error": True}, debug)
            if resp.status_code != 200 or not resp.text:
                continue

            m = _ACTIVITY_RE.search(str(resp.url))
            if m:
                activity = m.group(1)
            if not activity:
                m = _ACTIVITY_RE.search(resp.text)
                if m:
                    activity = m.group(1)

            if activity:
                break

        if not activity:
            logger.warning(f"Couldn't resolve activity URN for share_id={share_id}")
            return (None, None, debug)

        # Step 2: pull counts from the author-only analytics page
        analytics_url = (
            f"https://www.linkedin.com/analytics/post-summary/"
            f"urn:li:activity:{activity}/"
        )
        resp = await _fetch_html(client, analytics_url, li_at, jsessionid, raw_cookies)
        if resp is None:
            return (activity, None, debug)

        debug["tried"].append({
            "url": analytics_url, "status": resp.status_code,
            "final_url": str(resp.url), "body_len": len(resp.text or ""),
        })

        if resp.status_code in (401, 403):
            return (activity, {"_auth_error": True}, debug)
        if resp.status_code != 200 or not resp.text:
            logger.warning(f"Analytics page {resp.status_code} for activity:{activity}")
            return (activity, None, debug)

        html = resp.text

        # Detect the "redirected to login" stub even though status was 200
        if "/uas/login" in str(resp.url) or "/checkpoint/" in str(resp.url):
            logger.warning(
                f"Analytics redirected to login (final={resp.url}) — "
                f"cookies don't have analytics access"
            )
            return (activity, None, debug)

        engagement, seen = _classify_counts(html)

        if engagement is None:
            # Dump 200-char snippets around engagement keywords so we can see
            # what format the numbers actually live in (the page is an SPA
            # and may render counts in a non-JSON form).
            snippets = {}
            lower = html.lower()
            for kw in ("impressionscount", "reactionscount", "commentscount",
                       "numimpressions", "numreactions", "numcomments",
                       "totalsocialactivitycounts", "viewscount", "likescount",
                       "reposts", "shares"):
                idx = lower.find(kw)
                if idx >= 0:
                    snippets[kw] = html[max(0, idx-30):idx+200]
            if snippets:
                logger.info(f"Analytics keyword snippets for activity:{activity}:")
                for k, v in snippets.items():
                    logger.info(f"  [{k}] {v!r}")
            elif seen:
                logger.info(f"Analytics has numeric fields but no engagement match. "
                            f"Sample: {dict(list(seen.items())[:20])}")
            else:
                # No keywords found at all — likely SPA shell, data via XHR
                logger.info(f"Analytics has no engagement keywords in HTML. "
                            f"len={len(html)}. First 400 chars: {html[:400]!r}")

        logger.info(
            f"Analytics hit for activity:{activity} — engagement={engagement}, "
            f"html_len={len(html)}"
        )
        return (activity, engagement, debug)


async def resolve_activity_urn(li_at: str, jsessionid: str,
                               share_id: str,
                               raw_cookies: str = None) -> str | None:
    """Backwards-compat shim — only the URN."""
    activity, _, _ = await fetch_post_page(li_at, jsessionid, share_id, raw_cookies)
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


async def fetch_engagement(li_at: str, jsessionid: str, post_id: str,
                           raw_cookies: str = None) -> dict | None:
    """Get engagement counts via the analytics page parse path."""
    _, engagement, _ = await fetch_post_page(li_at, jsessionid, post_id, raw_cookies)
    if engagement:
        return engagement

    # Fallback: Voyager updateV2 (modern LinkedIn 404s here but harmless to try)
    async with httpx.AsyncClient() as client:
        for urn in _urn_candidates(post_id):
            encoded = quote(urn, safe="")
            url = f"https://www.linkedin.com/voyager/api/feed/updateV2/{encoded}"
            try:
                resp = await _voyager_get(client, url, li_at, jsessionid, raw_cookies)
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
                         limit: int = 20,
                         raw_cookies: str = None) -> list[dict]:
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
                resp = await _voyager_get(client, url, li_at, jsessionid, raw_cookies)
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
