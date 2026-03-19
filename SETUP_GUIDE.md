# LinkedIn Auto-Poster Bot — Setup Guide

## Architecture

```
Telegram Digests → Telethon Reader → Claude API → Draft Post
                                                       ↓
                                        Telegram Bot (approve/edit/reject)
                                                       ↓
                                              LinkedIn API → Published Post
```

**Schedule:** Mon, Wed, Fri at 10:00 Tashkent time (configurable)

---

## Step 1: Create LinkedIn Developer App

1. Go to https://developer.linkedin.com/
2. Click **Create App**
3. Fill in:
   - **App name:** `ZBS LinkedIn Poster` (or any name)
   - **LinkedIn Page:** Select your company page (or create one)
   - **App logo:** Upload any logo
   - **Legal agreement:** Accept
4. Click **Create App**

### Configure App Settings

5. Go to **Auth** tab:
   - Copy **Client ID** and **Client Secret** → save for `.env`
   - Under **OAuth 2.0 settings**, add Redirect URL:
     ```
     https://your-app.up.railway.app/linkedin/callback
     ```

6. Go to **Products** tab:
   - Request access to **Share on LinkedIn** (this gives `w_member_social` scope)
   - It may take a few minutes to be approved (usually instant for Share)

### Important Notes
- LinkedIn tokens last **60 days** — the bot will warn you when they're expiring
- If you need to re-auth, just use `/connect` again in Telegram

---

## Step 2: Railway Deployment

### Create Project

1. Go to https://railway.app
2. New Project → **Deploy from GitHub Repo**
3. Connect this repo

### Add PostgreSQL

4. In Railway project → **New** → **Database** → **PostgreSQL**
5. Copy `DATABASE_URL` from the PostgreSQL service variables

### Set Environment Variables

6. Click on your service → **Variables** → add all from `.env.example`:

```
TELEGRAM_BOT_TOKEN=<from @BotFather>
TELEGRAM_API_ID=<from my.telegram.org>
TELEGRAM_API_HASH=<from my.telegram.org>
TELEGRAM_STRING_SESSION=<your existing session>
TELEGRAM_ADMIN_ID=<your Telegram user ID>
DIGEST_CHANNEL=zbsnewz

LINKEDIN_CLIENT_ID=<from Step 1>
LINKEDIN_CLIENT_SECRET=<from Step 1>
LINKEDIN_REDIRECT_URI=https://<your-service>.up.railway.app/linkedin/callback

ANTHROPIC_API_KEY=<your key>

DATABASE_URL=<from PostgreSQL service>

POST_DAYS=0,2,4
POST_HOUR=10
```

### Deploy

7. Railway will auto-detect the Dockerfile and deploy
8. Note your service URL (e.g., `https://linkedin-bot-production-xxxx.up.railway.app`)
9. Update `LINKEDIN_REDIRECT_URI` with the actual URL
10. Also update the redirect URL in LinkedIn Developer App settings

---

## Step 3: Connect LinkedIn

1. Open Telegram → your bot
2. Send `/connect`
3. Click the authorization link
4. Authorize on LinkedIn
5. Bot will confirm: "✅ LinkedIn connected!"

---

## Usage

### Automatic Mode (scheduled)
- Bot fetches digests Mon/Wed/Fri at 10:00 UZT
- Generates post from recent content
- Sends you draft in Telegram with buttons:
  - ✅ **Approve & Post** — publishes to LinkedIn
  - 🔄 **Regenerate** — creates new version
  - ✏️ **Edit** — you send corrected text
  - ❌ **Reject** — archives draft

### Manual Mode
- `/write I realized today that building systems is more valuable than being the best executor`
- Bot generates a post from your thought
- Same approval flow

### Other Commands
- `/generate` — force generate from latest digests
- `/fetch` — pull latest digests from Telegram
- `/status` — check LinkedIn token & schedule

---

## Privacy Rules (built into the system)

The Claude prompt automatically strips:
- ❌ Specific revenue/budget numbers
- ❌ Client/brand names
- ❌ Team member names
- ❌ Internal metrics (followers, engagement)
- ❌ Project codenames
- ❌ Business relationship details

**Transforms private → universal:**
- "Signed deal with Brand X" → "When you land a partnership..."
- "Channel hit 25K" → "When audience growth starts compounding..."
- "Team member Z isn't delivering" → "Delegation without systems is just hoping"

---

## Customization

### Change posting schedule
Update `POST_DAYS` and `POST_HOUR` in Railway variables:
- `POST_DAYS=0,2,4` → Mon, Wed, Fri
- `POST_DAYS=1,3` → Tue, Thu
- `POST_HOUR=10` → 10:00 AM Tashkent

### Change digest source
Update `DIGEST_CHANNEL` to any Telegram channel/chat username or ID.

### Adjust post style
Edit the `SYSTEM_PROMPT` in `post_generator.py` — all style instructions are there.
