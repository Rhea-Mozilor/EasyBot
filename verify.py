"""verify.py — one-shot setup diagnostic.

Run with:    python3 verify.py

Tells us, in order:
  1. Can Python actually do HTTPS? (catches the macOS SSL cert issue)
  2. Are the three Slack tokens set, and does Slack accept the bot token?
  3. What is the bot's actual user_id and display name in your workspace?
  4. Is the Gemini API key present?

It does NOT start Socket Mode, so it exits in a couple of seconds.
"""
from __future__ import annotations

import os
import sys

# --------------------------------------------------------------------------- #
# 0. Load .env
# --------------------------------------------------------------------------- #
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as e:
    print("FAIL  python-dotenv not installed:", e)
    sys.exit(1)

bot_token  = os.environ.get("SLACK_BOT_TOKEN", "")
app_token  = os.environ.get("SLACK_APP_TOKEN", "")
signing    = os.environ.get("SLACK_SIGNING_SECRET", "")
gemini_key = os.environ.get("GEMINI_API_KEY", "")

print("=" * 60)
print(" EasyBot setup verification")
print("=" * 60)

# --------------------------------------------------------------------------- #
# 1. HTTPS sanity check
# --------------------------------------------------------------------------- #
print("\n[1] Testing HTTPS / SSL certificate chain...")
import urllib.request, ssl
try:
    urllib.request.urlopen("https://slack.com", timeout=10).read(64)
    print("  OK     Python can complete an HTTPS handshake.")
except ssl.SSLCertVerificationError as e:
    print("  FAIL   SSL certificate verification failed.")
    print("         ", e)
    print("\n         FIX (macOS, python.org installer):")
    print("           /Applications/Python\\ 3.14/Install\\ Certificates.command")
    sys.exit(1)
except Exception as e:
    print("  FAIL   ", type(e).__name__, str(e))
    sys.exit(1)

# --------------------------------------------------------------------------- #
# 2/3. Slack auth.test
# --------------------------------------------------------------------------- #
print("\n[2] Checking Slack tokens...")
checks = [
    ("SLACK_BOT_TOKEN", bot_token, "xoxb-"),
    ("SLACK_APP_TOKEN", app_token, "xapp-"),
    ("SLACK_SIGNING_SECRET", signing, ""),
]
for name, value, prefix in checks:
    if not value or value.startswith("replace-me") or value == "xoxb-replace-me" or value == "xapp-replace-me":
        print(f"  FAIL   {name} is empty or still the placeholder.")
        sys.exit(1)
    if prefix and not value.startswith(prefix):
        print(f"  FAIL   {name} should start with `{prefix}` but starts with `{value[:6]}…`")
        sys.exit(1)
    print(f"  OK     {name} present ({value[:7]}…{value[-4:]})")

print("\n[3] Calling Slack auth.test with the bot token...")
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

client = WebClient(token=bot_token)
try:
    resp = client.auth_test()
except SlackApiError as e:
    err = e.response.get("error", "unknown")
    print(f"  FAIL   Slack rejected the bot token: {err}")
    if err == "invalid_auth":
        print("         The xoxb- token is wrong, expired, or the app was uninstalled.")
        print("         Go to api.slack.com/apps → your app → Install App → Reinstall to Workspace,")
        print("         then copy the new Bot User OAuth Token into .env.")
    elif err == "not_authed":
        print("         No token was sent — re-check SLACK_BOT_TOKEN in .env.")
    sys.exit(1)

print("  OK     Slack accepted the bot token.")
print(f"           bot_id     = {resp['bot_id']}")
print(f"           user_id    = {resp['user_id']}")
print(f"           user(name) = {resp['user']}")
print(f"           team       = {resp['team']}")
print(f"           url        = {resp['url']}")
print()
print("  >>>  In Slack, you should be able to do:  /invite @" + resp["user"])
print("  >>>  If `@" + resp["user"] + "` autocompletes in the message box,")
print("       the app is installed and visible. If not, reinstall the app.")

# --------------------------------------------------------------------------- #
# 4. Gemini key sanity (no network call — just presence)
# --------------------------------------------------------------------------- #
print("\n[4] LLM provider")
provider = (os.environ.get("LLM_PROVIDER") or "gemini").lower()
print(f"  provider = {provider}")
if provider == "gemini":
    if not gemini_key or gemini_key.startswith("replace") or gemini_key == "dummy":
        print("  WARN   GEMINI_API_KEY is empty/placeholder. The bot will boot but")
        print("         summarization & matching will fail at runtime.")
    else:
        print(f"  OK     GEMINI_API_KEY present ({gemini_key[:6]}…{gemini_key[-4:]})")

print("\nAll critical checks passed. You can now run:  python3 app.py")
