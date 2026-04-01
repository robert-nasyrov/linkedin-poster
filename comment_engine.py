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

# Topics to search for — broad enough to find results
SEARCH_TOPICS = [
    "built AI automation for my business",
    "replaced employee with AI agent",
    "AI tools saving hours every week",
    "solopreneur using AI to scale",
    "small team outperforming big company with AI",
    "AI workflow automation real results",
    "quit my job to build AI products",
    "AI changed how I run my company",
    "media company using AI production",
    "one person business AI tools",
    "Claude Anthropic building real products",
    "AI automation agency founder",
    "bootstrapped startup AI tools",
    "Telegram bot for business",
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
        async with httpx.AsyncClient(timeout=60) as client:
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
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Find recent LinkedIn posts about: {topic}\n\n"
                                f"Do multiple searches:\n"
                                f"1. Search: {topic} site:linkedin.com after:2026-03-01\n"
                                f"2. Search: {topic} linkedin post this week\n"
                                f"3. Search: {topic} linkedin post March 2026\n\n"
                                f"Requirements:\n"
                                f"- ONLY posts from 2026. Absolutely NO posts from 2024, 2023, or earlier.\n"
                                f"- From individual people, NOT company pages\n"
                                f"- If a result shows a date from 2024 or 2023, SKIP IT completely\n"
                                f"- Better to return 1-2 fresh posts than 5 old ones\n\n"
                                f"For each post return:\n"
                                f"- LinkedIn URL\n"
                                f"- Author name and role\n"
                                f"- Summary (2-3 sentences)\n"
                                f"- Date\n\n"
                                f"Return ONLY a JSON array:\n"
                                f'[{{"url": "...", "author": "Name — Role", "summary": "...", "date": "March 2026"}}]\n\n'
                                f"If you genuinely cannot find any posts from 2026, return empty array []. Return ONLY valid JSON."
                            ),
                        }
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            
            text_parts = [b["text"] for b in data["content"] if b.get("type") == "text"]
            raw = " ".join(text_parts).strip()
            
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            start = cleaned.find("[")
            end = cleaned.rfind("]") + 1
            if start >= 0 and end > start:
                posts = json.loads(cleaned[start:end])
                # Double-check: filter out obviously old posts
                fresh = []
                for p in posts:
                    date = p.get("date", "").lower()
                    if any(y in date for y in ["2022", "2023", "2024"]):
                        continue
                    if "linkedin.com" in p.get("url", ""):
                        fresh.append(p)
                logger.info(f"Found {len(fresh)} fresh LinkedIn posts about '{topic}'")
                return fresh[:count]
            
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
