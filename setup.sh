#!/usr/bin/env bash
# EasyBot setup script — run once after cloning.
# Works on macOS and Linux. On Windows use Git Bash or WSL.
set -e

echo "=== EasyBot Setup ==="

# ── 1. Python version check ───────────────────────────────────────
PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.10+ is required but was not found."
    echo "Install it from https://www.python.org/downloads/"
    exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(sys.version_info.minor + sys.version_info.major * 10)")
if [ "$PY_VER" -lt 310 ]; then
    echo "ERROR: Python 3.10+ required. Found: $($PYTHON --version)"
    exit 1
fi
echo "✓ Python: $($PYTHON --version)"

# ── 2. Virtual environment ────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "→ Creating virtual environment..."
    $PYTHON -m venv .venv
fi

# Activate
if [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate   # Windows / Git Bash
else
    source .venv/bin/activate
fi
echo "✓ Virtual environment ready"

# ── 3. Install dependencies ───────────────────────────────────────
echo "→ Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# ── 4. Create .env if it doesn't exist ────────────────────────────
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "✓ Created .env from .env.example"
    echo ""
    echo "  ┌─────────────────────────────────────────────────────┐"
    echo "  │  ACTION REQUIRED: open .env and fill in your values  │"
    echo "  │                                                       │"
    echo "  │  Required:                                            │"
    echo "  │    SLACK_BOT_TOKEN      (xoxb-...)                   │"
    echo "  │    SLACK_APP_TOKEN      (xapp-...)                   │"
    echo "  │    SLACK_SIGNING_SECRET                              │"
    echo "  │    GEMINI_API_KEY       (or OPENAI_API_KEY)          │"
    echo "  │                                                       │"
    echo "  │  See README.md → Step 3 for where to find each one.  │"
    echo "  └─────────────────────────────────────────────────────┘"
    echo ""
else
    echo "✓ .env already exists (not overwritten)"
fi

# ── 5. Initialise the database ────────────────────────────────────
echo "→ Initialising database..."
python db_setup.py
echo "✓ Database ready"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Fill in .env with your Slack tokens and API key (if not done yet)"
echo "  2. Run: source .venv/bin/activate && python app.py"
echo ""
echo "See README.md for full instructions."
