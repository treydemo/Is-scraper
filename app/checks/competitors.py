"""Light audit of local competitors: the same comparable checks we run on the prospect, no browser."""
from __future__ import annotations

import asyncio

import httpx

from .. import config
from ..fetch import base_of, fetch_raw, fetch_text, visible_text
from . import schema as schema_mod
from .site import SEARCH_BOTS, robots_access


async def light_site(client: httpx.AsyncClient, url: str) -> dict:
    home, home_gpt, robots, llms = await asyncio.gather(
        fetch_raw(client, url), fetch_raw(client, url, config.GPTBOT_UA),
        fetch_text(client, f"{base_of(url)}/robots.txt"), fetch_text(client, f"{base_of(url)}/llms.txt"))
    if not home.ok:
        return {"url": url, "scanned": False, "reason": "bot protection" if home.walled else (home.error or f"HTTP {home.status}")}
    sch = schema_mod.analyze(schema_mod.merge([schema_mod.extract(home.html)]))
    rtxt = robots[2] if robots[0] == 200 and "<html" not in (robots[2] or "")[:300].lower() else ""
    access = robots_access(rtxt, home.final_url or url)
    allowed = sum(1 for v in access["search"].values() if v)
    raw_chars = len(visible_text(home_gpt.html)) if home_gpt.ok else 0
    q_heads = home.html.count("?</h2>") + home.html.count("?</h3>")
    return {
        "url": url, "scanned": True,
        "local_schema": sch["has_local"],
        "faq_schema": sch["has_faq"],
        "schema_types": sch["types"][:12],
        "ai_bots_allowed": f"{allowed}/{len(SEARCH_BOTS)}",
        "ai_bots_allowed_n": allowed,
        "gptbot_walled": home_gpt.walled,
        "raw_text_chars": raw_chars,
        "llms_txt": llms[0] == 200 and "<html" not in (llms[2] or "")[:300].lower() and len(llms[2].strip()) > 20,
        "question_headings_home": q_heads,
    }


def comparable_score(c: dict, sov: int | None) -> int:
    """Same yardstick for everyone (prospect included). 100 = maxed on every comparable signal."""
    pts, mx = 0.0, 0.0
    # Reviews & rating (Google)
    mx += 25
    rc = c.get("review_count") or 0
    pts += 15 * min(1.0, rc / 150)
    if c.get("rating"):
        pts += 10 * max(0.0, min(1.0, (c["rating"] - 3.5) / 1.4))
    site = c.get("site") or {}
    if site.get("scanned"):
        mx += 45
        pts += 12 if site.get("local_schema") else 0
        pts += 8 if site.get("faq_schema") else 0
        pts += 10 * (site.get("ai_bots_allowed_n", 0) / len(SEARCH_BOTS))
        pts += 10 * min(1.0, site.get("raw_text_chars", 0) / 3000)
        pts += 5 if site.get("llms_txt") else 0
    if sov is not None:
        mx += 30
        pts += 30 * min(1.0, sov / 60)
    return round(100 * pts / mx) if mx else 0
