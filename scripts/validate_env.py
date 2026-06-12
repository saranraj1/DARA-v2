"""
DARA — startup env validation.
Run before starting DARA to check all required env vars are set.
Usage:
    python scripts/validate_env.py          # interactive
    python scripts/validate_env.py --ci     # non-zero exit on failure (CI mode)
"""
from __future__ import annotations

import os
import sys
import argparse

sys.path.insert(0, ".")

RESET  = "\033[0m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"

def _ok(msg):  print(f"  \033[92m✓\033[0m  {msg}")
def _err(msg): print(f"  \033[91m✗\033[0m  {msg}")
def _warn(msg):print(f"  \033[93m⚠\033[0m  {msg}")


REQUIRED = [
    ("GROQ_API_KEY",           "Groq API key — console.groq.com (primary LLM)",     True),
    ("GITHUB_APP_ID",          "GitHub App ID — github.com/settings/apps",           True),
    ("GITHUB_WEBHOOK_SECRET",  "GitHub webhook HMAC secret",                          True),
    ("GITHUB_INSTALLATION_ID", "GitHub App installation ID",                          True),
    ("SLACK_BOT_TOKEN",        "Slack bot token (xoxb-…) — api.slack.com",           True),
    ("SLACK_SIGNING_SECRET",   "Slack signing secret — api.slack.com",               True),
]

OPTIONAL = [
    ("GOOGLE_API_KEY",         "Google Gemini API key (fallback LLM)"),
    ("HUGGINGFACE_API_TOKEN",  "HuggingFace token (embeddings fallback)"),
    ("NGROK_AUTHTOKEN",        "ngrok auth token (dev tunnel)"),
    ("NGROK_DOMAIN",           "ngrok static domain"),
    ("JIRA_API_TOKEN",         "JIRA integration (optional)"),
    ("PAGERDUTY_API_KEY",      "PagerDuty integration (optional)"),
]

DATABASE_DEFAULTS = [
    ("POSTGRES_URL",        "postgresql+asyncpg://dara:dara_dev@localhost:5432/dara"),
    ("REDIS_URL",           "redis://:dara_dev@localhost:6379/0"),
    ("QDRANT_URL",          "http://localhost:6333"),
    ("NEO4J_URI",           "bolt://localhost:7687"),
    ("ELASTICSEARCH_URL",   "http://localhost:9200"),
]


def check_env(ci_mode: bool) -> int:
    """Run all checks. Returns 0 on success, 1 if any required var is missing."""
    # Load .env if it exists
    env_file = ".env"
    if os.path.exists(env_file):
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
            print(f"  Loaded {env_file}\n")
        except ImportError:
            pass

    errors = 0

    print(f"{BOLD}── Required variables ──────────────────────────────────────────{RESET}")
    for var, description, required in REQUIRED:
        val = os.environ.get(var, "").strip()
        if val:
            # Mask value
            masked = val[:4] + "****" if len(val) > 4 else "****"
            _ok(f"{var} = {masked}")
        else:
            _err(f"{var} is MISSING — {description}")
            errors += 1

    # Slack token format check
    slack_token = os.environ.get("SLACK_BOT_TOKEN", "")
    if slack_token and not slack_token.startswith("xoxb-"):
        _err("SLACK_BOT_TOKEN must start with 'xoxb-'")
        errors += 1

    print(f"\n{BOLD}── Optional variables ──────────────────────────────────────────{RESET}")
    for var, description in OPTIONAL:
        val = os.environ.get(var, "").strip()
        if val:
            masked = val[:4] + "****" if len(val) > 4 else "****"
            _ok(f"{var} = {masked}")
        else:
            _warn(f"{var} not set ({description})")

    print(f"\n{BOLD}── Database URLs (defaults shown if not overridden) ─────────────{RESET}")
    for var, default in DATABASE_DEFAULTS:
        val = os.environ.get(var, "").strip()
        if val and val != default:
            _ok(f"{var} = (custom value set)")
        else:
            _warn(f"{var} using default: {default[:60]}")

    # LLM mode check
    print(f"\n{BOLD}── LLM configuration ───────────────────────────────────────────{RESET}")
    llm_mode  = os.environ.get("LLM_MODE", "groq").strip()
    groq_key  = os.environ.get("GROQ_API_KEY", "").strip()
    if llm_mode == "groq" and not groq_key:
        _err("LLM_MODE=groq but GROQ_API_KEY is not set")
        errors += 1
    elif llm_mode == "local":
        _warn("LLM_MODE=local — make sure Ollama is running at http://localhost:11434")
    else:
        _ok(f"LLM_MODE={llm_mode}")

    print()
    if errors == 0:
        print(f"  {GREEN}{BOLD}✓ All required environment variables are set. DARA is ready to start.{RESET}")
    else:
        print(f"  {RED}{BOLD}✗ {errors} required variable(s) missing. See .env.example for setup guide.{RESET}")
        print(f"  {YELLOW}  Hint: cp .env.example .env && nano .env{RESET}")

    return 0 if errors == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="DARA env validation")
    parser.add_argument("--ci", action="store_true", help="Exit with non-zero code on failure")
    args = parser.parse_args()

    print(f"\n{BOLD}DARA — Environment Validation{RESET}\n")
    exit_code = check_env(ci_mode=args.ci)

    if args.ci:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
