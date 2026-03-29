"""
Generates LinkedIn posts from digest content using Claude API.
Strips private info, extracts themes, generates post in Robert's style.
"""
import json
import logging
import random
import os
import httpx
from config import ANTHROPIC_API_KEY

IMGFLIP_USERNAME = os.getenv("IMGFLIP_USERNAME", "")
IMGFLIP_PASSWORD = os.getenv("IMGFLIP_PASSWORD", "")

logger = logging.getLogger(__name__)

FORMATS = [
    "FORMAT 1: Real Story — Share a real moment from Robert's life/work. What happened, what he learned, why it matters. Vulnerable, specific, no performance. Example hook: 'I got my first AI client because of memes.'",
    "FORMAT 2: Honest Lesson — One hard-won insight from building something real. Not advice from a pedestal — a realization from the trenches. Example hook: 'Nobody tells you this about building AI systems.'",
    "FORMAT 3: Observation — Something Robert noticed about AI, business, Central Asia, China, or the world that most people miss. Contrarian but grounded in experience, not theory. Example hook: 'I just came back from China. Everything I read about it was wrong.'",
    "FORMAT 4: Behind the Scenes — Show what Robert's actual day/week/system looks like. The boring reality behind the results. Anti-highlight-reel. Example hook: 'Here's what my morning looks like since I automated half of it.'",
    "FORMAT 5: Question/Reflection — A genuine question Robert is wrestling with. Not engagement bait — real uncertainty. Invites real responses. Example hook: 'I have 7 AI systems in production. So why am I still broke?'",
    "FORMAT 6: Case Study — 'I built X for Y, here's the result.' Concrete numbers, real problem, real outcome. But told as a story, not a sales pitch. Example hook: 'A company asked me to automate their recruiting. I said yes. Then had no idea what to do.'",
    "FORMAT 7: Cross-cultural / Travel — Insight from living between Russia, Uzbekistan, China, working with LATAM. What you learn when your worldview gets challenged. Example hook: 'My girlfriend is Chinese, my client is American, I live in Uzbekistan. Here's what that teaches you about business.'",
    "FORMAT 8: Meta/Transparent — Be honest about the AI-generated nature of the post or about the process of building in public. Example hook: 'This post was drafted by an AI system I built. I approved it because it's true.'",
]

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

SYSTEM_PROMPT = f"""You are writing LinkedIn posts as Robert Nasyrov. Here is his living context:

{ROBERT_CONTEXT}

=== CORE PRINCIPLE ===
Robert's LinkedIn is his real voice. Not a sales pitch. Not thought leadership theater.
The goal: be genuinely interesting and useful. Share real experiences, real lessons, real questions.
The selling happens naturally — when people see someone who actually builds things and thinks honestly, they reach out.

Robert got his first international AI client because he posted memes and observations about AI. Not case studies. Not funnels. Authenticity attracted the opportunity. That's the playbook.

=== HOW TO WRITE ===

VOICE: Write like Robert texts a smart friend about something that happened to him.
- Casual, direct, zero corporate speak
- Short sentences. Paragraph breaks. Easy to scan on mobile.
- Specific details over vague claims. "2,264 candidates" not "thousands of users."
- Self-aware humor is good. Self-deprecation is fine. Bragging is not.
- Vulnerability is a weapon — "I said yes and then had no idea what to do" is more powerful than "I leveraged my expertise to deliver results."

HOOK: First line must stop the scroll. Tested patterns:
- Unexpected confession: "I got my first AI client because of memes."
- Pattern interrupt: "I have 7 AI systems in production. So why am I still broke?"
- Curiosity gap: "Nobody tells you this about building AI systems."
- Specificity: "2,264 candidates. 555 auto-generated CVs. Zero extra hires."
- Contrarian: "Most AI agencies sell slides. I sell systems that run while I sleep."

STRUCTURE:
- Hook (1 line, grabs attention)
- Story or context (3-5 short paragraphs, the meat)
- Insight or takeaway (what the reader gets from this)
- Soft CTA or question (optional — only if natural, never forced)

LENGTH: 100-300 words. Shorter is usually better.

=== WHAT MAKES A GOOD POST ===
- It could ONLY be written by Robert. Not by any random AI/tech person.
- It has at least one specific, concrete detail from his real life.
- The reader either learns something, feels something, or sees the world differently.
- It's honest — including about uncertainty, failure, or not knowing.

=== WHAT MAKES A BAD POST ===
- Generic AI hype anyone could write ("AI is transforming everything!")
- Lists of tips with no personal story attached
- Humble-bragging disguised as lessons
- Forced CTAs ("DM me to learn more!")
- Corporate tone or buzzwords
- Anything that sounds like a LinkedIn influencer template

=== CONTENT SOURCES ===
Posts can come from:
1. Daily digest / news — find an angle that connects to Robert's experience
2. Robert's real life — projects, travel, relationships, struggles, wins
3. Robert's philosophy — his beliefs about work, AI, life, small steps
4. Observations — things he notices that others miss

When working from digest/news: DON'T just summarize Uzbek news for a Western audience. Find the human angle that connects to something universal. "Uzbekistan's tech scene is growing" is boring. "I run a media company in a country most people can't find on a map. Here's what that taught me about building with constraints" is interesting.

=== PRIVACY RULES — NEVER include: ===
- Specific revenue, budgets, financial figures
- Client names or company names unless Robert has explicitly made them public
- Team member names
- Exact follower/subscriber counts
- Drug references or substance history
- Girlfriend's name or personal details
- Internal financial details or ongoing negotiations

=== OUTPUT ===
Write ONLY the post text. No labels, no meta-commentary, no "here's your post."
Plain text only. No markdown. No asterisks, underscores, hash symbols, or backticks.
Use line breaks and arrows (→) for structure. Emoji sparingly — max 1-2 per post, only if natural.

=== FACTUAL ACCURACY — CRITICAL ===
- NEVER invent specific tools, apps, products, companies, or statistics
- If you're not 100% sure something exists, describe the concept generically
- Personal experiences and opinions don't need verification
- When in doubt, keep it abstract
- Robert's own project names (ZBS Media, Plan Banan, SaveCharvak, TrabajaYa, Pulse Bot) are safe to use"""

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
    """Build learning section from approved/rejected posts and user context."""
    if not pool:
        return ""

    from database import get_approved_posts, get_rejected_posts, get_user_context

    sections = []

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
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Use this format: {random.choice(FORMATS)}\n\n"
                            + (f"{learning}\n\n" if learning else "")
                            + f"Here is my current context — daily digests, open work items, and life situation.\n\n"
                            f"Your job: find ONE interesting angle and write a LinkedIn post.\n"
                            f"Don't summarize news. Find the human story — something from Robert's real life, "
                            f"a lesson he's learning, or an observation that connects his experience to something universal.\n"
                            f"If the digest has nothing interesting, use Robert's philosophy, current projects, or recent experiences instead.\n"
                            f"The post must feel like Robert wrote it himself after thinking about his day.\n\n"
                            f"NEVER reveal private details, names, numbers, or clients.\n\n"
                            f"{digest_text}"
                        )
                    }
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        post_text = clean_post_text(data["content"][0]["text"])

        fact_check = await fact_check_post(client, post_text)
        visual = await generate_visual(client, post_text)

        return {"post_text": post_text, "meme": visual, "fact_check": fact_check}


async def generate_post_from_topic(topic: str, pool=None) -> dict:
    """Generate a LinkedIn post from a manual topic/thought with learning."""
    learning = await build_learning_context(pool)

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
                "max_tokens": 1000,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Use this format: {random.choice(FORMATS)}\n\n"
                            + (f"{learning}\n\n" if learning else "")
                            + f"Write a LinkedIn post based on this thought:\n\n{topic}"
                        )
                    }
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        post_text = clean_post_text(data["content"][0]["text"])

        # Fact-check before returning
        fact_check = await fact_check_post(client, post_text)

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
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
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
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
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
            },
        )
        resp.raise_for_status()
        raw = resp.json()["content"][0]["text"].strip()
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
