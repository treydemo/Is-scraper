"""Runtime configuration. Everything comes from environment variables so Railway owns secrets."""
import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# Security
AUDIT_API_KEY = _env("AUDIT_API_KEY")            # required header X-API-Key on /jobs
CALLBACK_SECRET = _env("CALLBACK_SECRET")        # sent as X-Audit-Signature (HMAC-SHA256 of body)

# Where the service lives (used to build report/pdf links in callbacks)
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "https://is-scraper-production.up.railway.app").rstrip("/")

# Storage. Attach a Railway volume at /data to survive redeploys.
DATA_DIR = Path(_env("DATA_DIR", "/data" if Path("/data").exists() else "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Concurrency / limits
MAX_CONCURRENT_JOBS = int(_env("MAX_CONCURRENT_JOBS", "2"))
MAX_PAGES = int(_env("MAX_PAGES", "12"))
DEDUPE_DAYS = int(_env("DEDUPE_DAYS", "30"))
AI_QUERY_COUNT = int(_env("AI_QUERY_COUNT", "6"))
COMPETITOR_COUNT = int(_env("COMPETITOR_COUNT", "3"))

# Model providers. Any provider without a key is skipped and reported as "not measured".
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _env("CLAUDE_MODEL", "claude-sonnet-5-5")          # narrative + extraction
CLAUDE_FAST_MODEL = _env("CLAUDE_FAST_MODEL", "claude-haiku-5-5")  # parsing answers
CLAUDE_SEARCH_TOOL = _env("CLAUDE_SEARCH_TOOL", "web_search_20250305")

OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-5")
OPENAI_SEARCH_TOOL = _env("OPENAI_SEARCH_TOOL", "web_search")

GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")

PERPLEXITY_API_KEY = _env("PERPLEXITY_API_KEY")
PERPLEXITY_MODEL = _env("PERPLEXITY_MODEL", "sonar")

# Google: Places API (New), PageSpeed Insights, YouTube Data API all accept a standard API key
GOOGLE_API_KEY = _env("GOOGLE_API_KEY")

# Crawler identities
CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
GPTBOT_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; "
             "+https://openai.com/gptbot)")

BOOKING_URL = _env("BOOKING_URL", "https://immersiveagentics.com/connect/")
IA_SITE = _env("IA_SITE", "https://immersiveagentics.com")

VERSION = "5.0.0"
