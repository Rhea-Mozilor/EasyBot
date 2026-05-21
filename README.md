# EasyBot

A Slack bot that captures team decisions and Q&A from threads using a 🧠 reaction, summarises them with an LLM, stores them in SQLite, and quietly intercepts repeat questions in channels.

No Notion, no Jira — Slack only.

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 or higher |
| pip | bundled with Python |
| A Slack workspace | Admin or owner access to install an app |
| A Gemini **or** OpenAI API key | Free Gemini tier works |

---

## Quick start (5 steps)

### Step 1 — Clone and set up Python

```bash
git clone https://github.com/Rhea-Mozilor/EasyBot.git
cd EasyBot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Or run the included helper script:

```bash
bash setup.sh
```

---

### Step 2 — Create the Slack app

1. Go to <https://api.slack.com/apps> → **Create New App** → **From an app manifest**
2. Select your workspace
3. Paste the contents of `slack_manifest.yaml` and click **Next → Create**
4. On the app page, go to **OAuth & Permissions** → **Install to Workspace** → **Allow**

---

### Step 3 — Collect your 3 Slack tokens

| Token | Where to find it |
|---|---|
| `SLACK_BOT_TOKEN` (`xoxb-...`) | **OAuth & Permissions** → Bot User OAuth Token |
| `SLACK_APP_TOKEN` (`xapp-...`) | **Basic Information → App-Level Tokens** → Create token with scope `connections:write` |
| `SLACK_SIGNING_SECRET` | **Basic Information → App Credentials** |

---

### Step 4 — Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in your tokens and API key. At minimum you need:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
GEMINI_API_KEY=...          # get one free at https://aistudio.google.com/app/apikey
GEMINI_MODEL=gemini-2.5-flash-lite
```

---

### Step 5 — Initialise the database and run

```bash
python db_setup.py   # one-time — creates easybot.sqlite3
python app.py        # start the bot
```

You should see:

```
⚡️ Bolt app is running!
```

---

## Using the bot

### Capture a decision or Q&A

1. `/invite @EasyBot` into a channel
2. When a thread reaches a decision, the **channel creator** reacts with 🧠 on the message
3. EasyBot DMs them a draft summary → click **✅ Approve & Save**
4. The source message gets a ✅ reaction; the author gets a DM confirming their message was saved

### Intercept repeat questions

When anyone asks a question that matches a saved entry, EasyBot posts the answer publicly. High-confidence matches get the full card; medium-confidence shows a "possible match" suggestion with a confidence percentage.

### Search and browse

| Command | What it does |
|---|---|
| `@EasyBot list` | List all saved entries in this channel |
| `@EasyBot search <query>` | Keyword search in this channel |
| `@EasyBot search --global <query>` | Search across all channels |
| `/easybot list` | Same as above (requires slash command setup — see below) |
| `/easybot search <query>` | Same as above |

### Other features

- **👍 / 👎 feedback** — buttons on every match card; EasyBot thanks you ephemerally
- **View history** — click "📜 View history" on any match card to see the full audit trail
- **Update entry** — owner only; accessible from the history card
- **Delete entry** — owner only; accessible from the history card (permanent, with confirm dialog)
- **Staleness warning** — entries not updated in 90+ days get an ⚠️ banner automatically
- **Duplicate detection** — if a new capture is very similar to an existing entry, the owner is warned before approving
- **Author notification** — the person whose message was captured gets a DM when it's approved
- **Weekly digest** — every Sunday at 9am UTC, EasyBot DMs channel owners a summary of new entries and stale ones

---

## Setting up the /easybot slash command (optional but recommended)

After installing the app, add the slash command in the Slack dashboard:

1. Go to your app → **Slash Commands** → **Create New Command**
2. Command: `/easybot`
3. Short description: `Search and browse the knowledge base`
4. Usage hint: `list | search <query> | search --global <query>`
5. Save → reinstall the app if prompted

Without this, you can still use `@EasyBot list` and `@EasyBot search` instead.

---

## All environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SLACK_BOT_TOKEN` | ✅ | — | `xoxb-` bot token |
| `SLACK_APP_TOKEN` | ✅ | — | `xapp-` Socket Mode token |
| `SLACK_SIGNING_SECRET` | ✅ | — | App signing secret |
| `LLM_PROVIDER` | | `gemini` | `gemini` or `openai` |
| `GEMINI_API_KEY` | ✅ (Gemini) | — | Google AI Studio key |
| `GEMINI_MODEL` | | `gemini-1.5-flash` | Gemini model name |
| `OPENAI_API_KEY` | ✅ (OpenAI) | — | OpenAI key |
| `OPENAI_MODEL` | | `gpt-4o-mini` | OpenAI model name |
| `MYSQL_URL` | ✅ | — | Full MySQL connection URL (Railway injects this automatically) |
| `EASYBOT_CAPTURE_EMOJI` | | `brain` | Emoji that triggers capture (🧠) |
| `EASYBOT_APPROVED_EMOJI` | | `white_check_mark` | Emoji added to approved messages |
| `EASYBOT_MATCH_THRESHOLD` | | `0.5` | Min confidence to show any match |
| `EASYBOT_CONFIDENT_THRESHOLD` | | `0.75` | Min confidence for a "definite" match card |
| `EASYBOT_STALE_DAYS` | | `90` | Days before an entry is flagged as stale |
| `EASYBOT_DIGEST_WEEKDAY` | | `6` | Digest day (0=Mon … 6=Sun) |
| `EASYBOT_DIGEST_HOUR` | | `9` | Digest hour in UTC |

---

## Project layout

```
easybot/
├── app.py                  # Entry point — handler wiring + digest scheduler
├── db_setup.py             # One-time schema initializer
├── db.py                   # All SQLite query helpers
├── llm.py                  # LLM wrapper (Gemini default, OpenAI optional)
├── permissions.py          # Channel-type gate + owner check
├── views.py                # All Block Kit builders
├── handlers/
│   ├── capture.py          # Workflow 1: 🧠 reaction → approve/discard
│   ├── interception.py     # Workflow 2: message → match / fallback
│   ├── history.py          # Workflow 3: history card + update modal + delete
│   ├── slash.py            # /easybot search | list
│   ├── feedback.py         # 👍 / 👎 on match cards
│   └── digest.py           # Weekly digest background thread
├── slack_manifest.yaml     # Paste into Slack "From an app manifest"
├── setup.sh                # One-command setup script
├── requirements.txt
└── .env.example            # Copy to .env and fill in your values
```

---

## Keeping the bot running

For a personal or team deployment, the simplest approach is to run it in a `tmux` or `screen` session:

```bash
tmux new -s easybot
source .venv/bin/activate
python app.py
# Ctrl+B then D to detach
```

Or use `nohup`:

```bash
nohup python app.py > easybot.log 2>&1 &
```

For a production deployment, wrap it in a `systemd` service or deploy to a cloud VM (no public URL needed — EasyBot uses Socket Mode).

---

## Deploying to Railway

EasyBot runs as a **worker** (no HTTP port needed — it uses Slack Socket Mode).

### 1 — Push to GitHub

```bash
git push origin main
```

### 2 — Create a Railway project

1. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select your EasyBot repository
3. Railway auto-detects Python and builds with nixpacks

### 3 — Add a MySQL database

1. In your Railway project, click **+ New** → **Database** → **MySQL**
2. Railway creates the MySQL service and automatically injects `MYSQL_URL` into your worker — no manual copy-paste needed

### 4 — Set environment variables

In Railway → your worker service → **Variables**, add:

| Variable | Value |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-...` |
| `SLACK_APP_TOKEN` | `xapp-...` |
| `SLACK_SIGNING_SECRET` | your signing secret |
| `GEMINI_API_KEY` | your Google AI Studio key |

`MYSQL_URL` is injected automatically by the Railway MySQL addon. All other variables are optional (defaults work fine).

### 5 — Deploy

Railway deploys automatically on every push to `main`.  
Check **Logs** in the Railway dashboard — you should see:

```
⚡️ Bolt app is running!
```
