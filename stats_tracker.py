"""
Stats Tracker — fetches post engagement from LinkedIn and Threads.
Runs daily to update post_stats table.
"""
import logging
import httpx

logger = logging.getLogger(__name__)


async def fetch_linkedin_stats(access_token: str, share_id: str) -> dict:
    """Fetch likes, comments, shares for a LinkedIn post."""
    try:
        # LinkedIn Social Actions API
        encoded_urn = f"urn:li:share:{share_id}"
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
    """Fetch likes, replies, reposts, views for a Threads post."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://graph.threads.net/v1.0/{post_id}",
                params={
                    "fields": "likes,replies,reposts,views",
                    "access_token": access_token,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "likes": data.get("likes", {}).get("summary", {}).get("total_count", 0) if isinstance(data.get("likes"), dict) else data.get("likes", 0),
                    "comments": data.get("replies", {}).get("summary", {}).get("total_count", 0) if isinstance(data.get("replies"), dict) else data.get("replies", 0),
                    "shares": data.get("reposts", {}).get("summary", {}).get("total_count", 0) if isinstance(data.get("reposts"), dict) else data.get("reposts", 0),
                    "views": data.get("views", 0) if isinstance(data.get("views"), int) else 0,
                }
            else:
                logger.warning(f"Threads stats {resp.status_code} for {post_id}: {resp.text[:200]}")
                return None
    except Exception as e:
        logger.error(f"Threads stats error: {e}")
        return None


async def collect_all_stats(pool, linkedin_token: str = None, threads_token: str = None):
    """Collect stats for all recently posted content."""
    from database import get_posted_posts_for_stats, save_post_stats

    posts = await get_posted_posts_for_stats(pool)
    if not posts:
        logger.info("No posts to collect stats for")
        return

    updated = 0

    for post in posts:
        # LinkedIn stats
        if linkedin_token and post.get("linkedin_post_id"):
            stats = await fetch_linkedin_stats(linkedin_token, post["linkedin_post_id"])
            if stats:
                await save_post_stats(
                    pool, post["id"], "linkedin", post["linkedin_post_id"],
                    stats["likes"], stats["comments"], stats["shares"], stats["views"]
                )
                updated += 1

        # Threads stats
        if threads_token and post.get("threads_post_id"):
            stats = await fetch_threads_stats(threads_token, post["threads_post_id"])
            if stats:
                await save_post_stats(
                    pool, post["id"], "threads", post["threads_post_id"],
                    stats["likes"], stats["comments"], stats["shares"], stats["views"]
                )
                updated += 1

    logger.info(f"Updated stats for {updated} posts")
    return updated
