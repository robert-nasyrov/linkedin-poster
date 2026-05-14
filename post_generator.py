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

ENERGY_TYPES = [
    ("RAW_STORY",
     "RAW STORY: Something that actually happened today/this week. Messy details, "
     "real outcome. Lead with a SCENE — what you saw, what someone said, what broke. "
     "Then the realization. Length: 120-220 words."),
    ("HONEST_FAIL",
     "HONEST FAIL: Something you got wrong, what it cost, what you'd do differently. "
     "People remember vulnerability over wins. Lead with the failure outcome, then "
     "trace back. No false-modesty endings. Length: 100-200 words."),
    ("CONTRARIAN",
     "CONTRARIAN TAKE: You disagree with consensus and have lived experience to back it. "
     "Lead with the bold counter-claim, then the proof from your actual work. End with "
     "a sharp restatement, not a question. Length: 80-180 words."),
    ("BEHIND_SCENES",
     "BEHIND THE SCENES: Show the actual work. Real tools (Claude, aiogram, Railway, "
     "Whisper, asyncpg), real errors, real logs. Lead with the technical detail, "
     "let the lesson emerge from it. Length: 100-200 words."),
    ("OBSERVATION",
     "OBSERVATION: You noticed something subtle others missed. Lead with the specific "
     "observation, name the pattern, end with one line. Length: 50-100 words."),
    ("QUICK_THOUGHT",
     "QUICK THOUGHT: 2-4 sentences. Single idea, sharp delivery. No setup, no "
     "build-up. Just the thing. Length: 30-70 words."),
    ("SCENE_DIALOGUE",
     "SCENE WITH DIALOGUE: A real moment with someone said something memorable. "
     "Quote them. Then the meaning to you. Length: 100-180 words."),
    ("BEFORE_AFTER",
     "BEFORE/AFTER: Concrete state-change in your work or life. Numbers if you have "
     "them. Lead with the contrast, then the mechanism that caused it. Length: 80-180 words."),
]


SYSTEM_PROMPT = f"""You write LinkedIn posts as Robert. Not for Robert — AS him. His voice, his brain, his mess.

{ROBERT_CONTEXT}

=== MATERIAL DISCIPLINE — READ THIS FIRST ===

You may ONLY use stories, scenes, quotes, numbers, dialogue, names, and incidents that appear EXPLICITLY in the context provided to you (Robert's life digest, /context entries, top-performing posts, recent comments, talk-session transcripts, robert_context.md).

You MUST NOT invent:
- Client conversations ("my client said...")
- Specific bug scenarios you weren't told about
- Quotes from named people
- Numbers, percentages, durations
- Job/project specifics that aren't in the context

If the context has NO concrete recent scene or detail — write a SHORT observation (50-80 words) on a real theme from the context. Don't pad to 200 words with imagined detail. Length should match material density: thin material → short post.

The user has REPEATEDLY rejected drafts with "story is fake", "situation is fictional", "I don't have that in my actual experience". Material discipline is the single most important rule.

=== TOPIC ROTATION — READ THIS SECOND ===

Robert publishes one post at a time. If the context contains a "TOPIC LOCK" or "HARD TOPIC BLOCK" section, those topics are RECENTLY PUBLISHED. He will reject another post on the same subject — even a "different angle" on the same subject — even if the angle is genuinely fresh. He's said "I already posted about it" multiple times.

Before writing, scan the TOPIC LOCK section and pick a SUBJECT not listed there. The context will offer many real threads (his job search, his dad's English, TrabajaYa, Pulse Bot, Telegrad, debugging, his daughter, life in Tashkent, his clients, paywalls, AI hype takes, etc). Use one Robert hasn't published about in the last 30 days.

If literally every real subject in the context is in TOPIC LOCK, write a short observation (50-80 words) on something Robert mentioned in passing rather than recycling a published topic.

=== HOW TO WRITE ===

Forget templates. Forget "5 tips" and "here's what I learned." Write like Robert actually thinks — sometimes it's a 3-line observation, sometimes it's a 250-word story. Let the topic decide the length and shape.

OPENING LINE — this is the one thing Robert keeps rejecting drafts for. Make the FIRST sentence one of these patterns:
- A specific scene: "I'm reading code at 1AM when my bot pings me."
- A blunt confession: "I spent 25 touchpoints on a client. Zero closed."
- A counterintuitive claim: "Most automation projects fail because of the requirements doc."
- A specific number: "2,264 candidates went through the pipeline last week."
- A quote of someone else: "'Why can't your bot just understand?' my mom asked."
- A direct question that's actually weird: "When did I stop reading my own code?"
- A blunt one-line statement that opens a loop the reader has to follow.
NEVER open with "Here's the thing", "Let me tell you", "Game-changer", "Unpopular opinion:", "I've been thinking about", or any soft preamble.

ENERGY TYPES (the post should clearly inhabit ONE):
- Raw story: real scene, real outcome
- Honest fail: vulnerability over wins
- Observation: subtle pattern others miss, short
- Contrarian take: counter-claim with lived proof
- Behind the scenes: technical detail leading to a lesson
- Quick thought: 2-4 sentences, sharp delivery
- Scene with dialogue: someone said something memorable
- Before/after: concrete state-change with mechanism

WHAT MAKES ROBERT'S POSTS HIT:
- Specific > generic. "2,264 candidates processed" not "thousands of users"
- Stories > advice. Show what happened, let the reader draw conclusions
- Admit what you don't know or what failed
- Name real tools: Claude, Telegram, Railway, Python, aiogram, Whisper
- Reference real projects: Pulse Bot, TrabajaYa, ZBS Media, Plan Banan
- Don't always end with a question. Sometimes end with a statement. Or nothing.
- Vary length wildly. Some posts 50 words. Some 300. Never the same twice.

WHAT TO AVOID:
- The same opening pattern as recent posts (Robert WILL notice)
- Numbered lists as the whole post (unless it genuinely fits)
- Fake humility ("I'm no expert but...")
- Motivational poster energy
- Making up numbers, tools, clients, or stories. If it's not in the context above, don't invent it.
- Writing the same post STRUCTURE twice in a row (header → 3 examples → conclusion = banned if last post used it)

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

    from database import (
        get_approved_posts, get_rejected_posts, get_user_context,
        get_top_posts, get_low_engagement_posts, get_recent_comments,
        get_regen_feedback, get_recent_talk_transcripts, get_retired_topics,
    )
    from digest_reader import get_digest_context

    sections = []

    # === RETIRED TOPICS — absolute front. These are projects Robert explicitly
    # marked as "stop mentioning". Even if they appear in life context or in
    # robert_context.md, treat them as off-limits.
    try:
        retired = await get_retired_topics(pool)
        if retired:
            lines = []
            for r in retired:
                if r["note"]:
                    lines.append(f"- {r['keyword']} — {r['note']}")
                else:
                    lines.append(f"- {r['keyword']}")
            sections.append(
                "=== RETIRED — DO NOT MENTION THESE IN ANY POST ===\n"
                + "\n".join(lines)
            )
    except Exception as e:
        logger.warning(f"Retired topics load failed: {e}")

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

    # LOW-ENGAGEMENT POSTS — audience ignored these, avoid the pattern
    try:
        flops = await get_low_engagement_posts(pool, limit=3, min_age_days=3)
        if flops:
            examples = []
            for f in flops:
                platform = f.get("platform", "?")
                text = f.get("post_text", "")[:200]
                examples.append(f"[{platform}] {f.get('likes', 0)}❤️ {f.get('comments', 0)}💬\n{text}...")
            sections.append(
                "=== LOW-ENGAGEMENT POSTS (audience scrolled past — DON'T write like these) ===\n"
                + "\n---\n".join(examples)
            )
    except Exception as e:
        logger.warning(f"Low engagement load failed: {e}")

    # AUDIENCE COMMENTS — what people actually said
    try:
        recent_comments = await get_recent_comments(pool, limit=15, days=30)
        if recent_comments:
            lines = []
            for c in recent_comments:
                platform = c.get("platform", "?")
                author = c.get("author", "?")
                text = c.get("text", "").strip()[:200]
                post_preview = (c.get("post_text") or "")[:80].replace("\n", " ")
                lines.append(f"[{platform}] {author} on \"{post_preview}...\": {text}")
            sections.append(
                "=== WHAT READERS SAID IN COMMENTS (their actual words — mine for angles, "
                "objections, intent-to-buy signals) ===\n"
                + "\n".join(lines)
            )
    except Exception as e:
        logger.warning(f"Recent comments load failed: {e}")

    # === TOPIC LOCK ===
    # The single most common rejection signal in Robert's history is "I already
    # posted about this". Surface the last 10 PUBLISHED posts (not all
    # approved — specifically those that went out) as topics he must NOT cover
    # again until they age out. This goes BEFORE other learning so the model
    # sees it before anything that might pull it back to the same topic.
    approved = await get_approved_posts(pool, limit=10)
    if approved:
        topic_blocks = []
        for body in approved[:10]:
            preview = (body or "").strip()[:280].replace("\n", " ")
            topic_blocks.append(f"- {preview}")
        sections.append(
            "=== TOPIC LOCK — Robert PUBLISHED these in the last ~30 days. "
            "DO NOT write another post on the same topic, angle, or framing. "
            "He will reject it. Pick a topic these don't cover. ===\n"
            + "\n".join(topic_blocks)
        )

    # === HARD TOPIC BLOCKS from rejects ===
    # Rejects whose reason mentions "already / before / published / recently /
    # repeated" are explicit "this exact topic is taken" signals from Robert.
    # Treat them with longer retention and stronger language than generic rejects.
    rejected = await get_rejected_posts(pool, limit=10)
    if rejected:
        hard_blocks = []
        soft_rejects = []
        block_markers = ("already", "before", "published", "recently", "repeat",
                          "same", "had this kind", "wrote about", "posted about")
        for r in rejected:
            reason_l = (r["reason"] or "").lower()
            if any(m in reason_l for m in block_markers):
                hard_blocks.append(r)
            else:
                soft_rejects.append(r)

        if hard_blocks:
            blob = "\n---\n".join(
                f"POST: {r['text'][:300]}...\nWHY BLOCKED: {r['reason']}"
                for r in hard_blocks[:5]
            )
            sections.append(
                "=== HARD TOPIC BLOCK — Robert explicitly said 'I already posted "
                "about this' for these. Pick a DIFFERENT topic, not a different "
                "angle on the same one. ===\n" + blob
            )

        if soft_rejects:
            blob = "\n---\n".join(
                f"POST: {r['text'][:200]}...\nWHY REJECTED: {r['reason']}"
                for r in soft_rejects[:3]
            )
            sections.append(
                "=== REJECTED FOR STYLE/EXECUTION (not topic) — avoid this tone "
                "or framing in new posts ===\n" + blob
            )

    # Anti-repetition: surface the OPENING LINES of recent published posts.
    if approved:
        opens = []
        for body in approved[:10]:
            first_line = (body or "").strip().split("\n", 1)[0][:120]
            if first_line:
                opens.append(first_line)
        if opens:
            sections.append(
                "=== RECENT OPENING LINES — DO NOT OPEN A NEW POST WITH ANYTHING "
                "STRUCTURALLY SIMILAR TO ANY OF THESE ===\n"
                + "\n".join(f"- {o}" for o in opens)
            )

    # === STYLE REFERENCE only (NOT topic templates) ===
    # Approved posts as style/voice samples — the model should learn the VOICE,
    # not the SUBJECT. Topic lock above already forbids re-using subjects.
    if approved:
        style_samples = "\n---\n".join(approved[:2])
        sections.append(
            "=== VOICE/STYLE REFERENCE (study HOW Robert writes — sentence rhythm, "
            "specificity, punctuation. Do NOT reuse the TOPIC or OPENING of these. "
            "Topic lock above is enforced.) ===\n" + style_samples
        )

    # REGENERATE FEEDBACK — what Robert said when he asked to redo a draft.
    # These are stronger taste signals than rejects (he wanted the topic, just wrong execution).
    # draft_text is the EXACT text Robert was reacting to — without it the feedback is meaningless.
    try:
        regen = await get_regen_feedback(pool, limit=10, days=60)
        if regen:
            items = []
            for r in regen:
                draft = (r.get("draft_text") or "").strip()[:400]
                txt = (r.get("feedback_text") or "").strip()[:300]
                items.append(f"DRAFT ROBERT REACTED TO:\n{draft}\n\nWHAT ROBERT SAID: {txt}")
            sections.append(
                "=== REGEN FEEDBACK (Robert wanted the topic but disliked the execution — "
                "study what specifically he objected to in each draft) ===\n"
                + "\n---\n".join(items)
            )
    except Exception as e:
        logger.warning(f"Regen feedback load failed: {e}")

    ctx = await get_user_context(pool, limit=10)
    if ctx:
        items = "\n".join([f"- [{c['date']}] {c['text']}" for c in ctx])
        sections.append(f"=== RECENT CONTEXT UPDATES ===\n{items}")

    # Talk transcripts — what Robert literally said in /talk sessions recently.
    # Highest-fidelity source of real material; the discipline rule explicitly
    # whitelists these.
    try:
        talks = await get_recent_talk_transcripts(pool, limit=3, days=30)
        if talks:
            chunks = []
            for t in talks:
                lines = []
                for m in t["messages"]:
                    who = "BOT" if m["role"] == "bot" else "ROBERT"
                    lines.append(f"  {who}: {m['text']}")
                chunks.append(
                    f"Talk session {t['started_at'].date()}:\n" + "\n".join(lines)
                )
            sections.append(
                "=== TALK SESSIONS (Robert's literal recent words — TREAT AS "
                "PRIMARY MATERIAL FOR ANY POST. Do not paraphrase loosely; quote "
                "or stay close to his actual phrases) ===\n"
                + "\n\n".join(chunks)
            )
    except Exception as e:
        logger.warning(f"Talk transcripts load failed: {e}")

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


_OPENER_PROMPT = """You are warming Robert up to write a LinkedIn post by asking him ONE specific, probing question.

Look at the context above (his life digest, /context entries, top posts, talk history). Pick the SINGLE most interesting recent moment, frustration, decision, or change — something concrete and unwritten.

Ask ONE question — open enough to get a real story, specific enough that he can't dodge with vague answers. ONE SENTENCE. Direct. Personal.

GOOD examples:
- "What's the most absurd rejection reason you got from those LinkedIn job apps this week?"
- "Did anything specific break in TrabajaYa today, or was it a quiet day?"
- "Your dad's English progress — what did he say to you in his last lesson?"
- "When did you decide to stop tracking impressions and start watching comments instead?"
- "What part of the new bot are you avoiding writing because it scares you?"

BAD examples (forbidden):
- "How are things?"
- "What's new?"
- "Tell me about your week"
- "What are you working on?"
- Any opener that could apply to anyone

Return ONLY the question text. No greetings, no labels, no quotes around it."""


_FOLLOWUP_PROMPT = """Robert just answered. Your job: ask ONE follow-up question that pulls a SPECIFIC concrete detail out — a number, a name, a quote, a moment, a sensory detail, a mistake.

Don't repeat his words. Probe deeper.

GOOD follow-ups:
- "What did the user literally type in that case?"
- "Was that the moment you almost gave up on the project?"
- "How long did you actually stare at the log before you saw it?"
- "Who said that to you, and what was your reaction in the moment?"
- "What was the smallest detail you almost missed?"

BAD follow-ups (forbidden):
- "Interesting, tell me more"
- "What else?"
- "Why?" (too lazy)
- Anything generic

Return ONLY the question text. ONE SENTENCE."""


_FROM_TALK_PROMPT = """Below is a real Q&A conversation between Robert and the bot. Write a LinkedIn post grounded ONLY in what Robert literally said in his answers.

CRITICAL RULES:
- Use Robert's actual phrases and numbers from his answers — quote them when natural
- Do NOT invent additional scenes, dialogue, characters, or details he didn't mention
- If the material is thin (short or vague answers), write a SHORT post (50-80 words)
- If the material is rich (specific numbers, scenes, quotes, names), write a 150-250 word story
- Open with a line that reflects what Robert actually said — not a soft preamble

Conversation:
{transcript}

Now write the post."""


_SUGGEST_PROMPT = """You're helping Robert pick what to write a LinkedIn post about TODAY.

The context above contains his current life, the topics he ALREADY posted on (TOPIC LOCK), retired projects (DO NOT MENTION), reader comments, etc.

Propose exactly 5 fresh post angles. Each MUST:
- Be grounded in something specific from the context (a name, an event, a number, a quote, a struggle)
- Avoid every topic in TOPIC LOCK / HARD TOPIC BLOCK / RETIRED
- Have a concrete opening line, not "thoughts on X" generic
- Be different from each other in subject — not 5 variations of the same theme

Return ONLY a JSON array, no markdown:
[
  {"title": "5-10 word topic name", "hook": "the actual first line of the post", "why_fresh": "one sentence why this isn't a repeat"},
  ...
]"""


async def suggest_fresh_topics(pool) -> list[dict]:
    """Ask Claude to scan everything and propose 5 fresh post angles."""
    learning = await build_learning_context(pool) if pool else ""
    async with httpx.AsyncClient(timeout=60) as client:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1500,
            "messages": [
                {"role": "user", "content": f"{learning}\n\n{_SUGGEST_PROMPT}"}
            ],
        })
    raw = data["content"][0]["text"].strip()
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]") + 1
    try:
        return json.loads(cleaned[start:end])
    except Exception as e:
        logger.error(f"Suggest parse failed: {e}; raw: {raw[:400]}")
        return []


async def generate_opener_question(pool) -> str:
    learning = await build_learning_context(pool) if pool else ""
    async with httpx.AsyncClient(timeout=45) as client:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 200,
            "messages": [
                {"role": "user",
                 "content": (f"{learning}\n\n" if learning else "") + _OPENER_PROMPT}
            ],
        })
    return data["content"][0]["text"].strip().strip('"').strip()


async def generate_followup_question(messages: list[dict]) -> str:
    transcript = "\n".join(
        f"{'BOT' if m['role'] == 'bot' else 'ROBERT'}: {m['text']}"
        for m in messages
    )
    async with httpx.AsyncClient(timeout=45) as client:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 200,
            "messages": [
                {"role": "user", "content": f"{transcript}\n\n{_FOLLOWUP_PROMPT}"}
            ],
        })
    return data["content"][0]["text"].strip().strip('"').strip()


async def generate_post_from_conversation(messages: list[dict], pool=None) -> dict:
    """Build a post from a /talk transcript using Robert's actual words as primary material."""
    learning = await build_learning_context(pool) if pool else ""
    transcript = "\n".join(
        f"{'BOT' if m['role'] == 'bot' else 'ROBERT'}: {m['text']}"
        for m in messages
    )
    async with httpx.AsyncClient(timeout=90) as client:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1200,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user",
                 "content": ((f"{learning}\n\n" if learning else "")
                             + _FROM_TALK_PROMPT.format(transcript=transcript))}
            ],
        })
        post_text = clean_post_text(data["content"][0]["text"])

        await asyncio.sleep(2)
        fact_check = await fact_check_post(client, post_text)
        await asyncio.sleep(2)
        visual = await generate_visual(client, post_text)

        return {"post_text": post_text, "meme": visual, "fact_check": fact_check}


async def _generate_one_variant(client: httpx.AsyncClient, learning: str,
                                 topic_or_digest: str, energy_label: str,
                                 energy_hint: str, source_kind: str) -> dict | None:
    """Generate one post locked into a specific energy type. Used by /genvars."""
    user_msg_parts = [learning + "\n\n" if learning else ""]
    if source_kind == "digest":
        user_msg_parts.append(
            f"Here's my current context — daily digests, open work items, and life situation.\n\n"
            f"{topic_or_digest}\n\n"
        )
    else:
        user_msg_parts.append(f"Write a post based on this thought:\n\n{topic_or_digest}\n\n")
    user_msg_parts.append(
        f"=== FORMAT LOCK FOR THIS DRAFT ===\n"
        f"Use specifically the {energy_label} energy. Do NOT mix in other energies.\n"
        f"{energy_hint}\n\n"
        f"Write ONE post in that exact mode. Open with a line that fits the FORMAT LOCK, "
        f"not the soft preambles forbidden in the system prompt."
    )

    try:
        data = await claude_request(client, {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": "".join(user_msg_parts)}],
        })
        post_text = clean_post_text(data["content"][0]["text"])
        return {"post_text": post_text, "energy": energy_label}
    except Exception as e:
        logger.warning(f"Variant generation failed for {energy_label}: {e}")
        return None


async def generate_post_variants(topic_or_digest: str, pool=None,
                                  count: int = 3, source_kind: str = "topic") -> list[dict]:
    """Generate `count` distinct variants in parallel, each locked to a different
    ENERGY_TYPES entry. Returns list of {post_text, energy, meme, fact_check}.
    """
    learning = await build_learning_context(pool)
    chosen = random.sample(ENERGY_TYPES, min(count, len(ENERGY_TYPES)))

    async with httpx.AsyncClient(timeout=90) as client:
        # Phase 1: text variants in parallel
        text_tasks = [
            _generate_one_variant(client, learning, topic_or_digest, label, hint, source_kind)
            for (label, hint) in chosen
        ]
        text_results = await asyncio.gather(*text_tasks)

        # Phase 2: visuals + fact-check sequentially per variant (avoid rate-limit
        # bursts; each variant gets its own visual that matches its tone)
        out: list[dict] = []
        for r in text_results:
            if not r:
                continue
            await asyncio.sleep(2)
            try:
                visual = await generate_visual(client, r["post_text"])
            except Exception as e:
                logger.warning(f"visual gen failed for variant {r['energy']}: {e}")
                visual = {"source": "none"}
            await asyncio.sleep(2)
            try:
                fc = await fact_check_post(client, r["post_text"])
            except Exception as e:
                logger.warning(f"fact-check failed for variant {r['energy']}: {e}")
                fc = {"status": "error", "issues": [], "suggestion": str(e)}
            out.append({
                "post_text": r["post_text"],
                "energy": r["energy"],
                "meme": visual,
                "fact_check": fc,
            })

    return out


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
