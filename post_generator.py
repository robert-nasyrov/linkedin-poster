"""
Generates LinkedIn posts from digest content using Claude API.
Strips private info, extracts themes, generates post in Robert's style.
"""
import json
import logging
import random
import os
import asyncio
import httpx
from config import ANTHROPIC_API_KEY

IMGFLIP_USERNAME = os.getenv("IMGFLIP_USERNAME", "")
IMGFLIP_PASSWORD = os.getenv("IMGFLIP_PASSWORD", "")

logger = logging.getLogger(__name__)


async def claude_request(client, json_body, max_retries=3):
    """Make Claude API request with retry on 429/529."""
    for attempt in range(max_retries):
        try:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=json_body,
            )
            if resp.status_code in (429, 529):
                wait = 10 * (attempt + 1)
                logger.warning(f"Claude {resp.status_code}, waiting {wait}s (attempt {attempt+1}/{max_retries})")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 529) and attempt < max_retries - 1:
                wait = 10 * (attempt + 1)
                logger.warning(f"Claude {e.response.status_code}, waiting {wait}s")
                await asyncio.sleep(wait)
            else:
                raise
    raise Exception("Claude API: max retries exceeded")

# Load living context from file
def load_robert_context() -> str:
    """Read robert_context.md for up-to-date personal context."""
    for path in ["robert_context.md", "/app/robert_context.md"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            continue
    return ""

ROBERT_CONTEXT = load_robert_context()

SYSTEM_PROMPT = f"""You write LinkedIn posts as Robert. Not for Robert — AS him. His voice, his brain, his mess.

{ROBERT_CONTEXT}

=== HOW TO WRITE ===

Forget templates. Forget "5 tips" and "here's what I learned." Write like Robert actually thinks — sometimes it's a 3-line observation, sometimes it's a 250-word story. Let the topic decide the length and shape.

ENERGY TYPES (not templates — moods):
- Raw story: Something that actually happened. Messy details. Real outcome. "My dad postponed English for 20 years. My bot fixed that in 3 days."
- Honest fail: Something that went wrong and what it taught you. People remember vulnerability. "Spent 25 touchpoints on a client. Zero closed deals. Here's the one question I should have asked on day 1."
- Observation: You noticed something others didn't. Short. Punchy. No fluff. Can be 3 sentences.
- Contrarian take: You disagree with conventional wisdom and have experience to back it up. "Everyone says AI replaces jobs. I watched it create 3 new roles in my company."
- Behind the scenes: Show the actual work. Mention real tools, real errors, real logs. "My bot generated a post about apps that don't exist. Fact-checker caught it. Here's the screenshot."
- Quick thought: Sometimes the best post is 2-3 sentences. Don't pad it. Say the thing and stop.

WHAT MAKES ROBERT'S POSTS HIT:
- Specific > generic. "2,264 candidates processed" not "thousands of users"
- Stories > advice. Show what happened, let the reader draw conclusions
- Admit what you don't know or what failed
- Name real tools: Claude, Telegram, Railway, Python, aiogram, Whisper
- Reference real projects: Pulse Bot, TrabajaYa, ZBS Media, Plan Banan
- Don't always end with a question. Sometimes end with a statement. Or nothing.
- Vary length wildly. Some posts 50 words. Some 300. Never the same twice.

WHAT TO AVOID:
- "Here's the thing" / "Let me tell you" / "Game-changer" / "Unpopular opinion:" as an opener
- Numbered lists as the whole post (unless it genuinely fits)
- Fake humility ("I'm no expert but...")
- Motivational poster energy
- Making up numbers, tools, clients, or stories. If it's not in the context above, don't invent it.
- Writing the same post structure twice in a row

=== PRIVACY ===
- Never include: revenue figures, client names (unless public), team member names, financial details
- OK to mention: ZBS Media, Plan Banan, SaveCharvak, TrabajaYa, Pulse Bot — these are public

=== FACTUAL ACCURACY ===
- NEVER invent specific tools, apps, products, companies, or statistics
- If unsure something exists, describe the concept without naming it
- Personal experiences and opinions don't need verification

=== FORMAT ===
- English only
- NO markdown. No asterisks, no underscores, no hash symbols, no backticks
- Plain text only. Use line breaks and emoji sparingly for structure.
- Write ONLY the post text. No labels, no meta-commentary."""

MEME_PROMPT = """Based on this LinkedIn post, suggest exactly ONE meme concept for supermeme.ai.

Return ONLY a JSON object:
{
  "search_query": "2-4 word meme template name for supermeme.ai",
  "top_text": "short top text",
  "bottom_text": "short bottom text",
  "description": "one sentence why this meme fits"
}

Return ONLY valid JSON, no markdown, no backticks, no explanation."""


def clean_post_text(text: str) -> str:
    """Strip any markdown formatting that LinkedIn doesn't support."""
    import re
    # Remove bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # Remove italic *text* or _text_
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    # Remove headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove backticks
    text = text.replace('`', '')
    return text.strip()


async def build_learning_context(pool) -> str:
    """Build learning section from approved/rejected posts, user context, daily life, AND audience engagement."""
    if not pool:
        return ""

    from database import get_approved_posts, get_rejected_posts, get_user_context, get_top_posts
    from digest_reader import get_digest_context

    sections = []

    # What's happening in Robert's life (from Pulse Bot + digest DB)
    try:
        life = await get_digest_context()
        if life:
            sections.append(life)
    except Exception as e:
        logger.warning(f"Digest context load failed: {e}")

    # TOP PERFORMING POSTS — what the AUDIENCE likes (not just Robert)
    try:
        top = await get_top_posts(pool, limit=5)
        if top:
            examples = []
            for t in top:
                platform = t.get("platform", "?")
                likes = t.get("likes", 0)
                comments = t.get("comments", 0)
                shares = t.get("shares", 0)
                views = t.get("views", 0)
                text = t.get("post_text", "")[:250]
                score = t.get("engagement_score", 0)
                examples.append(
                    f"[{platform}] {likes} likes, {comments} comments, {shares} shares, {views} views (score: {score})\n{text}..."
                )
            sections.append(
                "=== TOP PERFORMING POSTS (audience loved these — write more like them) ===\n"
                + "\n---\n".join(examples)
            )
    except Exception as e:
        logger.warning(f"Top posts load failed: {e}")

    # What posts Robert approved/rejected (his taste)
    approved = await get_approved_posts(pool, limit=5)
    if approved:
        examples = "\n---\n".join(approved[:3])
        sections.append(f"=== POSTS ROBERT APPROVED (write more like these) ===\n{examples}")

    rejected = await get_rejected_posts(pool, limit=5)
    if rejected:
        examples = "\n---\n".join([f"POST: {r['text'][:200]}...\nWHY REJECTED: {r['reason']}" for r in rejected[:3]])
        sections.append(f"=== POSTS ROBERT REJECTED (avoid this style/tone/topic) ===\n{examples}")

    ctx = await get_user_context(pool, limit=10)
    if ctx:
        items = "\n".join([f"- [{c['date']}] {c['text']}" for c in ctx])
        sections.append(f"=== RECENT CONTEXT UPDATES ===\n{items}")

    return "\n\n".join(sections)


async def generate_post_from_digest(digest_text: str, pool=None) -> dict:
    """Generate a LinkedIn post from digest content with learning."""
    learning = await build_learning_context(pool)

    async with httpx.AsyncClient(timeout=60) as client:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        (f"{learning}\n\n" if learning else "")
                        + f"Here's my current context — daily digests, open work items, and life situation.\n\n"
                        f"{digest_text}\n\n"
                        f"Write a LinkedIn post. Find the most interesting angle in this context. "
                        f"Don't force a format — let the content decide if it's a story, observation, hot take, or quick thought. "
                        f"Make it feel like something I'd actually write, not something a bot generated."
                    )
                }
            ],
        })
        post_text = clean_post_text(data["content"][0]["text"])

        await asyncio.sleep(3)  # Avoid rate limit
        fact_check = await fact_check_post(client, post_text)

        await asyncio.sleep(3)
        visual = await generate_visual(client, post_text)

        return {"post_text": post_text, "meme": visual, "fact_check": fact_check}


async def generate_post_from_topic(topic: str, pool=None, feedback: str = None) -> dict:
    """Generate a LinkedIn post from a manual topic/thought with learning."""
    learning = await build_learning_context(pool)

    feedback_block = ""
    if feedback:
        feedback_block = (
            f"\n\nIMPORTANT — The previous version of this post was rejected. "
            f"Here's what was wrong: {feedback}\n"
            f"Write a DIFFERENT post that fixes this issue. Don't repeat the same structure or approach.\n\n"
        )

    async with httpx.AsyncClient(timeout=60) as client:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        (f"{learning}\n\n" if learning else "")
                        + feedback_block
                        + f"Write a LinkedIn post based on this thought:\n\n{topic}\n\n"
                        f"Don't force a template. If it's a short observation, keep it short. "
                        f"If it's a story, tell it properly. Let the thought decide the shape."
                    )
                }
            ],
        })
        post_text = clean_post_text(data["content"][0]["text"])

        await asyncio.sleep(3)
        fact_check = await fact_check_post(client, post_text)

        await asyncio.sleep(3)
        visual = await generate_visual(client, post_text)

        return {"post_text": post_text, "meme": visual, "fact_check": fact_check}


async def fact_check_post(client: httpx.AsyncClient, post_text: str) -> dict:
    """
    Fact-check a post using Claude with web search.
    Returns dict with verified/unverified claims and suggestions.
    """
    try:
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
                            "You are a fact-checker. Analyze this LinkedIn post and check ANY specific factual claims:\n"
                            "- Named tools, apps, products, companies\n"
                            "- Statistics or numbers\n"
                            "- Specific events or announcements\n"
                            "- Technical claims\n\n"
                            "Use web search to verify each claim. Then return ONLY a JSON object:\n"
                            '{\n'
                            '  "status": "clean" or "issues_found",\n'
                            '  "issues": [\n'
                            '    {"claim": "the specific claim", "verdict": "verified" or "unverified" or "fabricated", "note": "explanation"}\n'
                            '  ],\n'
                            '  "suggestion": "brief suggestion if issues found, empty string if clean"\n'
                            '}\n\n'
                            "If the post contains only opinions, personal experiences, or general statements — return status: clean with empty issues.\n"
                            "Return ONLY valid JSON, no markdown.\n\n"
                            f"POST:\n{post_text}"
                        ),
                    }
                ],
            },
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
        
        # Extract text from response (may have tool_use blocks mixed in)
        text_parts = [b["text"] for b in data["content"] if b.get("type") == "text"]
        raw = " ".join(text_parts).strip()
        
        # Try to parse JSON from response
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        # Find JSON in response
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end])
        
        return {"status": "clean", "issues": [], "suggestion": ""}
        
    except Exception as e:
        logger.error(f"Fact-check error: {e}")
        return {"status": "error", "issues": [], "suggestion": f"Fact-check failed: {e}"}


UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")


async def generate_visual(client: httpx.AsyncClient, post_text: str) -> dict:
    """
    Decide visual type and generate it.
    Types: meme (fun/ironic posts), photo (serious/professional), none (text-only)
    """
    # Ask Claude what visual fits best
    try:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Analyze this LinkedIn post and decide what visual to attach.\n\n"
                        "Return ONLY a JSON object:\n"
                        '{"type": "meme" or "photo" or "none", "search_query": "2-4 word search for Unsplash photo if type is photo"}\n\n'
                        "Rules:\n"
                        "- meme: for posts with irony, humor, hot takes, or listicles\n"
                        "- photo: for professional, serious, case study, or inspirational posts\n"
                        "- none: for short question posts or when text speaks for itself\n"
                        "- Vary your choices! Don't always pick the same type.\n"
                        "Return ONLY valid JSON.\n\n"
                        f"Post:\n{post_text}"
                    ),
                }
            ],
        })
        raw = data["content"][0]["text"].strip()
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        decision = json.loads(cleaned[start:end])
    except Exception as e:
        logger.error(f"Visual type decision error: {e}")
        decision = {"type": "meme", "search_query": ""}

    visual_type = decision.get("type", "meme")
    logger.info(f"Visual type decided: {visual_type}")

    # Generate based on type
    if visual_type == "photo" and UNSPLASH_ACCESS_KEY:
        photo = await search_unsplash_photo(client, decision.get("search_query", "technology"))
        if photo:
            return photo

    if visual_type == "none":
        return {"source": "none"}

    # Default to meme
    return await generate_meme_suggestion(client, post_text)


async def search_unsplash_photo(client: httpx.AsyncClient, query: str) -> dict:
    """Search Unsplash for a relevant photo."""
    try:
        resp = await client.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query": query,
                "per_page": 3,
                "orientation": "landscape",
            },
            headers={
                "Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])

        if results:
            # Pick random from top 3
            photo = random.choice(results[:3])
            image_url = photo["urls"]["regular"]
            photographer = photo["user"]["name"]
            unsplash_link = photo["links"]["html"]

            logger.info(f"Unsplash photo found: {image_url[:80]}...")
            return {
                "source": "unsplash",
                "image_url": image_url,
                "photographer": photographer,
                "unsplash_link": unsplash_link,
                "query": query,
            }
    except Exception as e:
        logger.error(f"Unsplash search error: {e}")

    return None


async def generate_meme_suggestion(client: httpx.AsyncClient, post_text: str) -> dict:
    """Generate a meme using free Imgflip API."""

    # Popular meme templates with IDs
    MEME_TEMPLATES = {
        "Drake Hotline Bling": "181913649",
        "Distracted Boyfriend": "112126428",
        "Two Buttons": "87743020",
        "Change My Mind": "129242436",
        "Expanding Brain": "93895088",
        "Is This A Pigeon": "100777631",
        "Waiting Skeleton": "4087833",
        "Running Away Balloon": "131087935",
        "Left Exit 12 Off Ramp": "124822590",
        "Buff Doge vs Cheems": "247375501",
        "Disaster Girl": "97984",
        "Clown Applying Makeup": "252600902",
        "Always Has Been": "252758727",
        "Trade Offer": "309868304",
        "Anakin Padme 4 Panel": "322841258",
        "This Is Fine": "55311130",
        "Tuxedo Winnie The Pooh": "222403160",
        "Sad Pablo Escobar": "174908189",
        "Think About It": "148715956",
        "One Does Not Simply": "61579",
        "Batman Slapping Robin": "438680",
        "Roll Safe Think About It": "89370399",
        "Gru's Plan": "131940431",
        "Train hitting bus": "247113703",
        "Boardroom Meeting Suggestion": "440381756",
        "They're The Same Picture": "180190441",
        "Surprised Pikachu": "155067746",
        "Panik Kalm Panik": "226297822",
        "Monkey Puppet": "148909805",
        "Woman Yelling At Cat": "188390779",
        "Epic Handshake": "135256802",
        "Bike Fall": "43601446",
        "Bernie I Am Once Again": "91545132",
        "Spider-Man Double": "363474466",
        "Hide the Pain Harold": "27813981",
        "Mocking SpongeBob": "102156234",
        "Success Kid": "61544",
        "Ancient Aliens": "101470",
        "Stonks": "52223427",
        "Sleeping Shaq": "99683372",
    }

    # Pick 12 random templates to show Claude — prevents always picking the same ones
    selected = dict(random.sample(list(MEME_TEMPLATES.items()), min(12, len(MEME_TEMPLATES))))
    template_list = "\n".join([f"- {name}" for name in selected.keys()])

    # Step 1: Ask Claude to pick template + write text
    try:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Based on this LinkedIn post, create a meme.\n\n"
                        f"Available templates:\n{template_list}\n\n"
                        f"Return ONLY a JSON object:\n"
                        f'{{"template": "exact template name from list", "text0": "top text (short)", "text1": "bottom text (short)"}}\n\n'
                        f"Pick the most fitting template. Keep texts under 8 words each. Be funny.\n"
                        f"Return ONLY valid JSON, no markdown.\n\n"
                        f"Post:\n{post_text}"
                    ),
                }
            ],
        })
        raw = data["content"][0]["text"].strip()
        cleaned = raw.replace("```json", "").replace("```", "").strip()
        meme_data = json.loads(cleaned)
    except Exception as e:
        logger.error(f"Claude meme pick error: {e}")
        return {"source": "fallback", "description": "Could not generate meme"}

    template_name = meme_data.get("template", "Drake Hotline Bling")
    template_id = MEME_TEMPLATES.get(template_name, "181913649")
    text0 = meme_data.get("text0", "")
    text1 = meme_data.get("text1", "")

    # Step 2: Generate meme image via Imgflip API (free)
    if IMGFLIP_USERNAME and IMGFLIP_PASSWORD:
        try:
            meme_resp = await client.post(
                "https://api.imgflip.com/caption_image",
                data={
                    "template_id": template_id,
                    "username": IMGFLIP_USERNAME,
                    "password": IMGFLIP_PASSWORD,
                    "text0": text0,
                    "text1": text1,
                },
                timeout=15,
            )
            result = meme_resp.json()

            if result.get("success"):
                image_url = result["data"]["url"]
                return {
                    "source": "imgflip",
                    "template": template_name,
                    "text0": text0,
                    "text1": text1,
                    "image_url": image_url,
                }
            else:
                logger.error(f"Imgflip error: {result.get('error_message')}")
        except Exception as e:
            logger.error(f"Imgflip API error: {e}")

    # Fallback: return text-only suggestion
    return {
        "source": "claude_suggestion",
        "template": template_name,
        "text0": text0,
        "text1": text1,
        "description": f"Use '{template_name}' meme template",
    }
