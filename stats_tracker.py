"""
Stats Tracker — fetches post engagement from LinkedIn and Threads.
Runs daily to update post_stats table.
"""
import logging
import httpx

logger = logging.getLogger(__name__)


def _linkedin_urn(share_id: str) -> str:
    """Accept either a raw numeric id or a full URN like 'urn:li:share:...' / 'urn:li:ugcPost:...'."""
    sid = (share_id or "").strip()
    return sid if sid.startswith("urn:li:") else f"urn:li:share:{sid}"


async def fetch_linkedin_stats(access_token: str, share_id: str) -> dict:
    """Fetch likes, comments, shares for a LinkedIn post."""
    try:
        encoded_urn = _linkedin_urn(share_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.linkedin.com/v2/socialActions/{encoded_urn}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "likes": data.get("likesSummary", {}).get("totalLikes", 0),
                    "comments": data.get("commentsSummary", {}).get("totalFirstLevelComments", 0),
                    "shares": data.get("shareCount", 0),
                    "views": 0,  # LinkedIn doesn't expose views via this endpoint
                }
            else:
                logger.warning(f"LinkedIn stats {resp.status_code} for {share_id}")
                return None
    except Exception as e:
        logger.error(f"LinkedIn stats error: {e}")
        return None


async def fetch_threads_stats(access_token: str, post_id: str) -> dict:
    """Fetch Threads engagement metrics via the /insights endpoint.

    likes/views/reposts/quotes/replies are NOT fields on the post object —
    they are insight metrics. Requires the access token to have
    `threads_manage_insights` scope (older tokens issued without it will 4xx).
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://graph.threads.net/v1.0/{post_id}/insights",
                params={
                    "metric": "views,likes,replies,reposts,quotes",
                    "access_token": access_token,
                },
            )
            if resp.status_code != 200:
                logger.warning(
                    f"Threads insights {resp.status_code} for {post_id}: {resp.text[:200]}"
                )
                return None

            data = resp.json()
            metrics = {}
            for item in data.get("data", []):
                name = item.get("name")
                # Threads returns either values:[{value: N}] or total_value:{value: N}
                val = 0
                if isinstance(item.get("values"), list) and item["values"]:
                    val = item["values"][0].get("value", 0) or 0
                elif isinstance(item.get("total_value"), dict):
                    val = item["total_value"].get("value", 0) or 0
                metrics[name] = val

            return {
                "likes": metrics.get("likes", 0),
                "comments": metrics.get("replies", 0),
                "shares": metrics.get("reposts", 0) + metrics.get("quotes", 0),
                "views": metrics.get("views", 0),
            }
    except Exception as e:
        logger.error(f"Threads insights error: {e}")
        return None


async def fetch_linkedin_comments(access_token: str, share_id: str) -> list:
    """Fetch first-level comments on a LinkedIn post. Returns [] on failure."""
    try:
        urn = _linkedin_urn(share_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.linkedin.com/v2/socialActions/{urn}/comments",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
            if resp.status_code != 200:
                logger.warning(f"LinkedIn comments {resp.status_code} for {share_id}: {resp.text[:200]}")
                return []
            data = resp.json()
            out = []
            for el in data.get("elements", []):
                msg = el.get("message", {})
                text = msg.get("text", "") if isinstance(msg, dict) else ""
                if not text:
                    continue
                author = el.get("actor", "unknown")
                cid = el.get("id") or el.get("$URN") or f"{share_id}:{len(out)}"
                out.append({"id": str(cid), "author": str(author), "text": text})
            return out
    except Exception as e:
        logger.error(f"LinkedIn comments error: {e}")
        return []


async def fetch_threads_comments(access_token: str, post_id: str) -> list:
    """Fetch replies to a Threads post. Returns [] on failure (needs threads_manage_replies scope)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://graph.threads.net/v1.0/{post_id}/replies",
                params={
                    "fields": "id,text,username,timestamp",
                    "access_token": access_token,
                },
            )
            if resp.status_code != 200:
                logger.warning(f"Threads comments {resp.status_code} for {post_id}: {resp.text[:200]}")
                return []
            data = resp.json()
            out = []
            for el in data.get("data", []):
                text = el.get("text", "")
                if not text:
                    continue
                out.append({
                    "id": str(el.get("id", "")),
                    "author": el.get("username", "unknown"),
                    "text": text,
                })
            return out
    except Exception as e:
        logger.error(f"Threads comments error: {e}")
        return []


async def _collect_linkedin_via_voyager(pool, post, li_at, jsessionid) -> tuple[bool, bool]:
    """Fallback path for LinkedIn engagement using browser cookies.
    Returns (collected, auth_error). auth_error=True means cookies are stale —
    caller should stop calling Voyager and notify the user.

    LinkedIn's share/ugcPost numeric IDs do NOT equal activity URN IDs.
    On first call for a post we resolve the activity URN via redirect
    crawl and cache it on linkedin_posts so subsequent calls are 1 request.
    """
    from database import save_post_stats, save_post_comment, set_linkedin_activity_urn
    from linkedin_voyager import (
        fetch_engagement, fetch_comments, resolve_activity_urn, jitter,
    )

    activity_urn = post.get("linkedin_activity_urn")
    if not activity_urn:
        resolved = await resolve_activity_urn(li_at, jsessionid, post["linkedin_post_id"])
        if resolved:
            activity_urn = resolved
            await set_linkedin_activity_urn(pool, post["id"], activity_urn)
            logger.info(f"Resolved activity URN {activity_urn} for post #{post['id']}")
        else:
            logger.warning(f"Could not resolve activity URN for post #{post['id']}")
            return (False, False)

    target = f"urn:li:activity:{activity_urn}"
    stats = await fetch_engagement(li_at, jsessionid, target)
    if stats and stats.get("_auth_error"):
        return (False, True)
    if not stats:
        return (False, False)

    await save_post_stats(
        pool, post["id"], "linkedin", post["linkedin_post_id"],
        stats["likes"], stats["comments"], stats["shares"], stats["views"]
    )
    if stats["comments"] > 0:
        for c in await fetch_comments(li_at, jsessionid, target):
            await save_post_comment(
                pool, post["id"], "linkedin", c["id"], c["author"], c["text"]
            )
    await jitter()
    return (True, False)


async def collect_all_stats(pool, linkedin_token: str = None, threads_token: str = None):
    """Collect stats + comment content for all recently posted content.

    LinkedIn flow: try the official `/v2/socialActions` endpoint first; on
    failure (typical for personal apps in 2024+), fall back to Voyager via
    the user's browser cookies if any are stored. If Voyager auth breaks,
    skip remaining LinkedIn requests and surface the failure.
    """
    from database import (
        get_posted_posts_for_stats, save_post_stats, save_post_comment,
        get_linkedin_cookies,
    )

    posts = await get_posted_posts_for_stats(pool)
    if not posts:
        logger.info("No posts to collect stats for")
        return {"updated": 0, "li_cookies_stale": False}

    cookies = await get_linkedin_cookies(pool)
    li_at = cookies.get("li_at") if cookies else None
    jsessionid = cookies.get("jsessionid") if cookies else None

    updated = 0
    li_cookies_stale = False

    for post in posts:
        # LinkedIn — official API first, Voyager fallback
        if post.get("linkedin_post_id"):
            collected = False
            if linkedin_token:
                stats = await fetch_linkedin_stats(linkedin_token, post["linkedin_post_id"])
                if stats:
                    await save_post_stats(
                        pool, post["id"], "linkedin", post["linkedin_post_id"],
                        stats["likes"], stats["comments"], stats["shares"], stats["views"]
                    )
                    updated += 1
                    collected = True
                    if stats["comments"] > 0:
                        for c in await fetch_linkedin_comments(linkedin_token, post["linkedin_post_id"]):
                            await save_post_comment(
                                pool, post["id"], "linkedin", c["id"], c["author"], c["text"]
                            )

            if not collected and li_at and not li_cookies_stale:
                ok, auth_err = await _collect_linkedin_via_voyager(
                    pool, post, li_at, jsessionid
                )
                if ok:
                    updated += 1
                if auth_err:
                    li_cookies_stale = True

        # Threads — only one path, Graph API insights
        if threads_token and post.get("threads_post_id"):
            stats = await fetch_threads_stats(threads_token, post["threads_post_id"])
            if stats:
                await save_post_stats(
                    pool, post["id"], "threads", post["threads_post_id"],
                    stats["likes"], stats["comments"], stats["shares"], stats["views"]
                )
                updated += 1
                if stats["comments"] > 0:
                    for c in await fetch_threads_comments(threads_token, post["threads_post_id"]):
                        await save_post_comment(
                            pool, post["id"], "threads", c["id"], c["author"], c["text"]
                        )

    logger.info(f"Updated stats for {updated} posts (li_cookies_stale={li_cookies_stale})")
    return {"updated": updated, "li_cookies_stale": li_cookies_stale}
