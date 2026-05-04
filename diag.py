"""
Diagnostics: read-only check of what's actually in the DB.
Run via:  railway run python diag.py
(railway run injects DATABASE_URL and other env vars from the linked project)
"""
import asyncio
import os

import asyncpg


async def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set. Run via 'railway run python diag.py'")
        return

    conn = await asyncpg.connect(db_url, timeout=15)

    print("=" * 60)
    print("POSTS")
    print("=" * 60)
    rows = await conn.fetch(
        """SELECT status, COUNT(*) AS n
           FROM linkedin_posts GROUP BY status ORDER BY n DESC"""
    )
    for r in rows:
        print(f"  {r['status']:10s} {r['n']}")

    print()
    print("Posts with reject_reason filled:",
          await conn.fetchval(
              "SELECT COUNT(*) FROM linkedin_posts WHERE reject_reason IS NOT NULL"))
    print("Posts with linkedin_post_id:",
          await conn.fetchval(
              "SELECT COUNT(*) FROM linkedin_posts WHERE linkedin_post_id IS NOT NULL"))
    print("Posts with threads_post_id:",
          await conn.fetchval(
              "SELECT COUNT(*) FROM linkedin_posts WHERE threads_post_id IS NOT NULL"))

    print()
    print("=" * 60)
    print("ENGAGEMENT (post_stats)")
    print("=" * 60)
    rows = await conn.fetch(
        """SELECT platform,
                  COUNT(*) AS rows_,
                  COALESCE(SUM(likes),0)    AS likes,
                  COALESCE(SUM(comments),0) AS comments,
                  COALESCE(SUM(shares),0)   AS shares,
                  COALESCE(SUM(views),0)    AS views,
                  MAX(updated_at)           AS last_updated
           FROM post_stats GROUP BY platform"""
    )
    if not rows:
        print("  (empty)  <-- engagement collection never wrote anything")
    for r in rows:
        print(f"  {r['platform']:10s} rows={r['rows_']:3d}  "
              f"❤️{r['likes']}  💬{r['comments']}  🔄{r['shares']}  👁{r['views']}  "
              f"last_updated={r['last_updated']}")

    print()
    print("=" * 60)
    print("COMMENTS (post_comments)")
    print("=" * 60)
    rows = await conn.fetch(
        """SELECT platform, COUNT(*) AS n FROM post_comments GROUP BY platform"""
    )
    if not rows:
        print("  (empty)  <-- no comments ever harvested")
    for r in rows:
        print(f"  {r['platform']:10s} {r['n']}")

    last_comments = await conn.fetch(
        """SELECT platform, author, LEFT(text, 80) AS preview, created_at
           FROM post_comments ORDER BY created_at DESC LIMIT 5"""
    )
    if last_comments:
        print("  Last 5 comments:")
        for c in last_comments:
            print(f"    [{c['platform']}] {c['author']}: {c['preview']}  ({c['created_at']})")

    print()
    print("=" * 60)
    print("REJECT REASONS (latest 10)")
    print("=" * 60)
    rows = await conn.fetch(
        """SELECT id, LEFT(reject_reason, 120) AS reason, created_at
           FROM linkedin_posts
           WHERE status='rejected' AND reject_reason IS NOT NULL
           ORDER BY created_at DESC LIMIT 10"""
    )
    if not rows:
        print("  (no reject reasons saved)")
    for r in rows:
        print(f"  #{r['id']}  {r['created_at'].date()}  {r['reason']}")

    print()
    print("=" * 60)
    print("LATEST POSTED (last 10)")
    print("=" * 60)
    rows = await conn.fetch(
        """SELECT id, posted_at, linkedin_post_id, threads_post_id
           FROM linkedin_posts WHERE status='posted'
           ORDER BY posted_at DESC LIMIT 10"""
    )
    for r in rows:
        li = "✓" if r['linkedin_post_id'] else "—"
        th = "✓" if r['threads_post_id'] else "—"
        print(f"  #{r['id']}  posted={r['posted_at']}  LI={li}  TH={th}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
