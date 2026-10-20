"""Site-level measurements that need no API keys: AI crawler access, JS dependency, content shape."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..crawl import SiteData, parse_date
from ..fetch import visible_text
from .google import digits, name_tokens

# Bots that fetch pages to answer live questions (these decide whether you can be cited today)
SEARCH_BOTS = {
    "OAI-SearchBot": "ChatGPT search",
    "ChatGPT-User": "ChatGPT browsing",
    "PerplexityBot": "Perplexity",
    "Claude-SearchBot": "Claude search",
    "Googlebot": "Google + AI Overviews",
    "Bingbot": "Bing + Copilot",
}
# Bots that collect training data (blocking them is a policy choice; we report, lightly weighted)
TRAINING_BOTS = {
    "GPTBot": "OpenAI training",
    "ClaudeBot": "Anthropic training",
    "Google-Extended": "Gemini training",
    "CCBot": "Common Crawl",
    "Applebot-Extended": "Apple Intelligence",
}


def robots_access(robots_txt: str, url: str) -> dict:
    if not robots_txt:
        return {"has_robots": False, "search": {b: True for b in SEARCH_BOTS},
                "training": {b: True for b in TRAINING_BOTS}}
    rp = robotparser.RobotFileParser()
    rp.parse(robots_txt.splitlines())
    path = url
    return {
        "has_robots": True,
        "search": {b: rp.can_fetch(b, path) for b in SEARCH_BOTS},
        "training": {b: rp.can_fetch(b, path) for b in TRAINING_BOTS},
    }


def js_dependency(raw_html: str, rendered_text: str, name: str, phone: str) -> dict:
    raw_text = visible_text(raw_html)
    rl, dl = len(raw_text), len(rendered_text or "")
    ratio = round(rl / dl, 2) if dl else None
    raw_lower = raw_text.lower()
    toks = name_tokens(name)
    name_in_raw = bool(toks) and all(t in raw_lower for t in toks)
    phone_in_raw = bool(digits(phone)) and digits(phone) in digits(raw_text) if phone else None
    h1_raw = bool(BeautifulSoup(raw_html or "", "lxml").find("h1"))
    return {"raw_chars": rl, "rendered_chars": dl, "ratio": min(ratio, 1.0) if ratio is not None else None,
            "name_in_raw": name_in_raw, "phone_in_raw": phone_in_raw, "h1_in_raw": h1_raw,
            "raw_excerpt": raw_text[:400]}


def meta_flags(html: str, headers: dict) -> dict:
    soup = BeautifulSoup(html or "", "lxml")
    robots_meta = " ".join(m.get("content", "") for m in soup.find_all("meta", attrs={"name": re.compile("robots", re.I)}))
    xrobots = headers.get("x-robots-tag", "")
    canon = soup.find("link", rel=lambda v: v and "canonical" in v)
    title = soup.title.get_text(strip=True) if soup.title else ""
    desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    return {"noindex": "noindex" in (robots_meta + " " + xrobots).lower(),
            "canonical": canon.get("href") if canon else None,
            "title": title, "meta_description": desc.get("content", "").strip() if desc else ""}


def content_shape(site: SiteData, texts: dict[str, str], htmls: dict[str, str]) -> dict:
    q_heads, all_heads = [], 0
    video = False
    testimonial_hits = 0
    for u, html in htmls.items():
        soup = BeautifulSoup(html or "", "lxml")
        for h in soup.find_all(["h2", "h3", "h4", "summary", "dt", "button"]):
            t = h.get_text(" ", strip=True)
            if 8 < len(t) < 160:
                all_heads += 1 if h.name in ("h2", "h3", "h4") else 0
                if t.endswith("?"):
                    q_heads.append(t)
        if soup.find("iframe", src=re.compile(r"youtube|vimeo|wistia|loom", re.I)) or soup.find("video"):
            video = True
        txt = texts.get(u, "").lower()
        testimonial_hits += len(re.findall(r"testimonial|what (our )?(clients|customers) say|★★★★★|5 stars|google review", txt))
    q_heads = list(dict.fromkeys(q_heads))

    def kind_count(k):
        urls = set(site.page_kinds.get(k, []))
        # don't count the hub page itself, count children like /services/x
        children = {u for u in urls if len([p for p in urlparse(u).path.split("/") if p]) >= 2}
        return max(len(children), 1 if urls else 0)

    newest, newest_url = None, None
    for u in site.sitemap_urls:
        d = parse_date(u.get("lastmod"))
        if d and (newest is None or d > newest):
            newest, newest_url = d, u["loc"]
    fresh_days = (datetime.now(timezone.utc) - newest).days if newest else None
    about_texts = [texts[u] for u in texts if re.search(r"about|our-story|who-we-are|team|leader", u)]
    return {
        "question_headings": q_heads[:40],
        "question_heading_count": len(q_heads),
        "heading_count": all_heads,
        "faq_page": bool(site.page_kinds.get("faq")),
        "service_pages": kind_count("services"),
        "area_pages": kind_count("areas"),
        "proof_pages": kind_count("proof"),
        "testimonial_signals": testimonial_hits,
        "blog": bool(site.page_kinds.get("blog")),
        "newest_update_days": fresh_days,
        "newest_update_url": newest_url,
        "sitemap_url_count": len(site.sitemap_urls),
        "about_page": bool(about_texts) and max(len(t) for t in about_texts) > 400,
        "video_on_site": video,
    }


SOCIAL_PATTERNS = {
    "facebook": r"facebook\.com/(?!sharer|share|dialog|plugins|tr\b)[\w.\-/]+",
    "instagram": r"instagram\.com/(?!p/|explore)[\w.\-]+",
    "linkedin": r"linkedin\.com/(?:company|in)/[\w.\-]+",
    "youtube": r"youtube\.com/(?:@[\w.\-]+|channel/[\w\-]+|c/[\w.\-]+|user/[\w.\-]+)",
    "tiktok": r"tiktok\.com/@[\w.\-]+",
    "x": r"(?:twitter|x)\.com/(?!intent|share|home)[\w]+",
    "yelp": r"yelp\.com/biz/[\w\-]+",
    "nextdoor": r"nextdoor\.com/pages/[\w\-]+",
}


def social_links(htmls: dict[str, str], same_as: list[str], given: dict[str, str]) -> dict:
    found: dict[str, str] = {}
    blob = " ".join(htmls.values()) + " " + " ".join(same_as)
    for k, pat in SOCIAL_PATTERNS.items():
        m = re.search(pat, blob, re.I)
        if m:
            found[k] = "https://" + m.group(0).rstrip("/\"'")
    for k, v in (given or {}).items():
        if v and k not in found:
            found[k] = v
    return found
