# LinkedIn Auto-Poster — AI Content Pipeline

An autonomous content pipeline that generates LinkedIn posts in the owner's voice, sends them for approval via Telegram, and publishes to LinkedIn. Self-improving system that learns from approved and rejected examples.

**Live in production** · Running on Railway · Hands-free content pipeline

## What it does

- Reads news, context, and trends from configured sources
- Generates posts matching the owner's writing voice and style
- Sends drafts to Telegram for one-tap approve/reject
- Publishes approved posts to LinkedIn via API
- Stores approved and rejected examples to improve future output
- Threads integration for cross-posting

## How it works

```
News/Context sources → Claude API (generate post in owner's voice)
        ↓
Telegram approval flow (approve / reject / edit)
        ↓
   [approved] → LinkedIn API (/v2/shares) → published
   [rejected] → stored as negative example → improves future prompts
        ↓
Learning system: approved + rejected examples → better generations over time
```

## Stack

- **Python 3.11**
- **Claude API** (Anthropic) — content generation
- **LinkedIn API** (`/v2/shares`, Person URN)
- **Telegram Bot** (aiogram) — approval workflow
- **PostgreSQL** — context storage, examples, learning data
- **Railway** — deployment with auto-deploy from GitHub

## Key feature: Learning loop

The system maintains a database of approved and rejected posts. Each new generation includes recent examples as context, so the AI adapts to what performs well and avoids patterns that get rejected. Output quality improves over time without manual prompt tuning.

## Context injection

A living profile document is injected into the system prompt, giving Claude full context about the author's background, projects, tone, and current priorities. This ensures posts feel authentic rather than generic. The context file is excluded from the repo for privacy.
