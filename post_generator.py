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
    "FORMAT 1: Case Study — 'I built X — here's what it does and why it matters'",
    "FORMAT 2: Before vs Now — 'This used to take hours. Now it takes seconds.'",
    "FORMAT 3: Hot Take — 'Unpopular opinion about AI/media/business'",
    "FORMAT 4: Scannable List — '5 things I learned building X'",
    "FORMAT 5: Question Post — short case + genuine question for comments",
    "FORMAT 6: Meta/Transparent — 'This post was written by AI and I just approved it. Here's why that matters.'",
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

SYSTEM_PROMPT = f"""You are a LinkedIn ghostwriter for Robert. Here is his living context — updated regularly from real conversations:

{ROBERT_CONTEXT}

=== THE SELLING ANGLE ===
Robert's LinkedIn is not a diary. It's a sales channel for AI automation services.
Every post should make readers think: "Wait, one person built all this? I need this for my business."
Target audience: founders, COOs, agency owners, media companies.

=== POST FORMATS (rotate between these) ===

FORMAT 1: Case Study — "I built X, here's what it does"
- Problem → what you built → result → "If you're doing X manually, there's a better way."
- This is the MONEY format. Use it most often.

FORMAT 2: Before vs Now
- Show the transformation with specific details and time savings.

FORMAT 3: Hot Take
- Contrarian statement backed by personal experience. Invite disagreement.

FORMAT 4: Scannable List
- "5 things I automated this year" — each item concrete with real result.

FORMAT 5: Question Post
- Brief case study + genuine question. LinkedIn algorithm loves comments.

FORMAT 6: Meta/Transparent
- Be honest this post was AI-generated and you approved it. Demonstrates the product.

=== WRITING STYLE ===
- Language: ENGLISH only
- Tone: Casual, direct, real. Like texting a smart friend about work.
- Length: 100–300 words
- Uses "—" em dashes
- Concrete over abstract. Numbers over adjectives. Results over philosophy.
- Name real tools: Claude, Telegram, Whisper, Railway, Python
- OK to mention ZBS Media, Plan Banan, SaveCharvak by name — public brands
- OK to say "my team of 15" or "bootstrapped" — this is positioning

=== PRIVACY RULES — NEVER include: ===
- Specific revenue, budgets, financial figures
- Client names unless explicitly public
- Team member names
- Exact follower/subscriber counts
- Internal financial details

=== OUTPUT ===
Pick the format that fits best. Default to FORMAT 1 if unsure.
Write ONLY the post text. No labels, no meta-commentary.

CRITICAL: LinkedIn does NOT support markdown. NEVER use asterisks, underscores, hash symbols, or backticks. Plain text only. Use line breaks and emoji for structure."""

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


async def generate_post_from_digest(digest_text: str) -> dict:
    """Generate a LinkedIn post from digest content."""
    async with httpx.AsyncClient(timeout=60) as client:
        # Generate post
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
                            f"Here is my current context — daily digests, open work items, and life situation. "
                            f"Extract something interesting and write a LinkedIn post. "
                            f"Pick a theme that connects to broader trends (AI, media, entrepreneurship, productivity). "
                            f"Remember: NEVER reveal private details, names, numbers, or clients.\n\n"
                            f"{digest_text}"
                        )
                    }
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        post_text = clean_post_text(data["content"][0]["text"])

        # Generate meme suggestion
        meme = await generate_meme_suggestion(client, post_text)

        return {"post_text": post_text, "meme": meme}


async def generate_post_from_topic(topic: str) -> dict:
    """Generate a LinkedIn post from a manual topic/thought."""
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
                        "content": f"Use this format: {random.choice(FORMATS)}\n\nWrite a LinkedIn post based on this thought:\n\n{topic}"
                    }
                ],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        post_text = clean_post_text(data["content"][0]["text"])

        meme = await generate_meme_suggestion(client, post_text)

        return {"post_text": post_text, "meme": meme}


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
    }

    template_list = "\n".join([f"- {name}" for name in MEME_TEMPLATES.keys()])

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
