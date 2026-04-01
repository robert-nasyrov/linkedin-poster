"""
LinkedIn Comment Engine
- Finds relevant LinkedIn posts via web search
- Generates smart comments based on Robert's expertise
- Posts comments via LinkedIn API
"""
import json
import logging
import os
import random
import httpx
from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

# Topics to search for — specific and current
SEARCH_TOPICS = [
    "AI automation agency 2026",
    "built AI bot for business",
    "Claude API production use case",
    "solopreneur AI tools 2026",
    "small team AI replacing hiring",
    "AI media production workflow",
    "Telegram bot business automation 2025 2026",
    "no-code AI automation results",
    "AI replacing marketing agencies",
    "one person AI startup",
    "AI content creation real results",
    "building with Claude Anthropic",
    "n8n automation business",
    "AI operations small company",
]

# Load context for comment style
def load_comment_context() -> str:
    for path in ["robert_context.md", "/app/robert_context.md"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()[:2000]
        except FileNotFoundError:
            continue
    return ""

ROBERT_CONTEXT = load_comment_context()

COMMENT_SYSTEM_PROMPT = f"""You generate LinkedIn comments for Robert.

{ROBERT_CONTEXT[:1500]}

COMMENT RULES:
- Write as Robert — casual, direct, real
- Add value: share a relevant experience, ask a smart question, or offer a different angle
- Reference your actual experience building AI systems for media companies
- Keep it 2-5 sentences. Not too short (looks lazy), not too long (looks try-hard)
- NEVER be generic ("Great post!", "Thanks for sharing!", "So true!")
- NEVER be sycophantic or over-complimentary
- Sound like a real person who actually read the post and has something to add
- Sometimes respectfully disagree or offer a counterpoint
- Occasionally mention what you've built (naturally, not forcefully)
- Write in English

EXAMPLES OF GOOD COMMENTS:
- "This matches what I've seen running media ops in Uzbekistan. We automated our news pipeline with Claude API and the bottleneck shifted from production to editorial judgment. The tools are fast — knowing what to say is still slow."
- "Interesting take. I'd push back slightly — for small teams (<20 people), the ROI on AI automation is even higher because every hour saved is felt immediately. Built 7 bots for our media company and the compound effect is wild."
- "The delegation point resonates. I tried hiring more people first, then building systems. Systems won every time — they don't forget, don't get sick, and they scale without meetings."

Write ONLY the comment text. No quotes, no labels."""


async def find_linkedin_posts(count: int = 5, custom_topic: str = None) -> list:
    """Search for relevant LinkedIn posts using Claude web search."""
    topic = custom_topic or random.choice(SEARCH_TOPICS)
    
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
                    "max_tokens": 1500,
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Search for RECENT LinkedIn posts (from the last 30 days, 2026) about: {topic}\n\n"
                                f"Use search queries like: site:linkedin.com/posts {topic} 2026\n\n"
                                f"CRITICAL: Only include posts from 2025-2026. Skip anything older.\n"
                                f"Look for posts with real engagement (comments, likes) from individual people, NOT company pages.\n\n"
                                f"Find up to {count} posts. For each post return:\n"
                                f"- The LinkedIn post URL\n"
                                f"- Author name and title\n"  
                                f"- A brief summary of what the post says (2-3 sentences)\n\n"
                                f"Return ONLY a JSON array:\n"
                                f'[{{"url": "https://linkedin.com/posts/...", "author": "Name — Title", "summary": "What the post is about"}}]\n\n'
                                f"If you can't find recent posts, return an empty array []. Return ONLY valid JSON."
                            ),
                        }
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            
            # Extract text from response
            text_parts = [b["text"] for b in data["content"] if b.get("type") == "text"]
            raw = " ".join(text_parts).strip()
            
            # Parse JSON
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start = cleaned.find("[")
            end = cleaned.rfind("]") + 1
            if start >= 0 and end > start:
                posts = json.loads(cleaned[start:end])
                # Filter to only linkedin.com URLs
                posts = [p for p in posts if "linkedin.com" in p.get("url", "")]
                logger.info(f"Found {len(posts)} LinkedIn posts about '{topic}'")
                return posts[:count]
            
            return []
            
    except Exception as e:
        logger.error(f"Post search error: {e}")
        return []


async def generate_comment(post_url: str, post_summary: str) -> str:
    """Generate a relevant comment for a LinkedIn post."""
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
                    "max_tokens": 500,
                    "system": COMMENT_SYSTEM_PROMPT,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Write a comment for this LinkedIn post:\n\n"
                                f"URL: {post_url}\n"
                                f"Summary: {post_summary}\n\n"
                                f"Write a natural, value-adding comment. 2-5 sentences."
                            ),
                        }
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Comment generation error: {e}")
        return ""


async def generate_comment_from_url(post_url: str) -> dict:
    """Fetch post content via web search and generate comment."""
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            # Fetch post content
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1000,
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Read this LinkedIn post and summarize it in 3-4 sentences:\n{post_url}\n\n"
                                f"Then write a relevant comment from Robert's perspective (AI automation builder, media operator).\n\n"
                                f"Return ONLY a JSON object:\n"
                                f'{{"summary": "post summary", "comment": "your generated comment"}}\n\n'
                                f"Return ONLY valid JSON."
                            ),
                        }
                    ],
                    "system": COMMENT_SYSTEM_PROMPT,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            
            text_parts = [b["text"] for b in data["content"] if b.get("type") == "text"]
            raw = " ".join(text_parts).strip()
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(cleaned[start:end])
            
            return {"summary": "Could not read post", "comment": ""}
            
    except Exception as e:
        logger.error(f"Comment from URL error: {e}")
        return {"summary": f"Error: {e}", "comment": ""}


async def post_comment_to_linkedin(access_token: str, post_urn: str, comment_text: str, person_urn: str) -> dict:
    """Post a comment on a LinkedIn post."""
    payload = {
        "actor": person_urn,
        "message": {
            "text": comment_text
        }
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.linkedin.com/v2/socialActions/{post_urn}/comments",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if resp.status_code == 201:
            logger.info(f"Comment posted on {post_urn}")
            return {"success": True}
        else:
            logger.error(f"Comment failed: {resp.status_code} {resp.text}")
            return {"success": False, "error": resp.text}
