"""Site discovery: robots, llms.txt, sitemaps, and the handful of pages that matter for an audit."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import config
from .fetch import RawPage, base_of, domain_of, fetch_raw, fetch_text

PAGE_KINDS: dict[str, list[str]] = {
    "about":      ["about", "our-story", "who-we-are", "mission", "company"],
    "team":       ["team", "leadership", "staff", "our-people", "meet-", "trainers", "attorneys", "doctors", "providers"],
    "services":   ["service", "what-we-do", "programs", "treatments", "practice-areas", "solutions", "offerings"],
    "areas":      ["service-area", "areas-we-serve", "locations", "location", "areas-served", "cities"],
    "faq":        ["faq", "frequently-asked", "questions"],
    "proof":      ["case-stud", "testimonial", "reviews", "success-stor", "results", "portfolio"],
    "blog":       ["blog", "news", "articles", "insights", "resources"],
    "contact":    ["contact", "get-in-touch", "book", "schedule"],
}


@dataclass
class SiteData:
    url: str
    base: str
    domain: str
    home_chrome: RawPage | None = None
    home_gptbot: RawPage | None = None
    robots_status: int | None = None
    robots_txt: str = ""
    llms_status: int | None = None
    llms_txt: str = ""
    sitemap_urls: list[dict] = field(default_factory=list)   # [{loc, lastmod}]
    sitemap_found: bool = False
    pages: dict[str, RawPage] = field(default_factory=dict)  # url -> raw page (Chrome UA)
    page_kinds: dict[str, list[str]] = field(default_factory=dict)  # kind -> urls
    nav_links: list[dict] = field(default_factory=list)      # [{href, text}]


def classify(url: str, text: str = "") -> list[str]:
    path = urlparse(url).path.lower()
    blob = f"{path} {text.lower()}"
    kinds = []
    for kind, keys in PAGE_KINDS.items():
        if any(k in blob for k in keys):
            kinds.append(kind)
    return kinds


def parse_sitemap(xml: str) -> tuple[list[dict], list[str]]:
    """Returns (urls, child_sitemaps)."""
    soup = BeautifulSoup(xml, "xml")
    children = [loc.get_text(strip=True) for sm in soup.find_all("sitemap") for loc in sm.find_all("loc")]
    urls = []
    for u in soup.find_all("url"):
        loc = u.find("loc")
        if not loc:
            continue
        lm = u.find("lastmod")
        urls.append({"loc": loc.get_text(strip=True), "lastmod": lm.get_text(strip=True) if lm else None})
    return urls, children


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s.replace("Z", "+00:00"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_links(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    dom = domain_of(base)
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = urljoin(base + "/", a["href"].strip())
        p = urlparse(href)
        if p.scheme not in ("http", "https") or domain_of(href) != dom:
            continue
        clean = f"{p.scheme}://{p.netloc}{p.path}".rstrip("/") or href
        if clean in seen or re.search(r"\.(pdf|jpg|jpeg|png|gif|webp|svg|zip|mp4)$", p.path, re.I):
            continue
        seen.add(clean)
        out.append({"href": clean, "text": a.get_text(" ", strip=True)[:80]})
    return out


async def _load_sitemaps(client: httpx.AsyncClient, base: str, robots_txt: str) -> tuple[bool, list[dict]]:
    candidates = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_txt or "")
    candidates += [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/wp-sitemap.xml"]
    seen, urls, found = set(), [], False
    queue = list(dict.fromkeys(candidates))
    fetched = 0
    while queue and fetched < 12 and len(urls) < 3000:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        status, ctype, text = await fetch_text(client, sm)
        fetched += 1
        if status != 200 or "<" not in text[:200] or ("urlset" not in text and "sitemapindex" not in text):
            continue
        found = True
        u, children = parse_sitemap(text)
        urls.extend(u)
        queue.extend(c for c in children if c not in seen)
    # de-dupe while keeping order
    dedup, keep = set(), []
    for u in urls:
        if u["loc"] not in dedup:
            dedup.add(u["loc"])
            keep.append(u)
    return found, keep


async def discover(client: httpx.AsyncClient, url: str) -> SiteData:
    base = base_of(url)
    site = SiteData(url=url, base=base, domain=domain_of(url))

    home_chrome, home_gpt, robots, llms = await asyncio.gather(
        fetch_raw(client, url, config.CHROME_UA),
        fetch_raw(client, url, config.GPTBOT_UA),
        fetch_text(client, f"{base}/robots.txt"),
        fetch_text(client, f"{base}/llms.txt"),
    )
    site.home_chrome, site.home_gptbot = home_chrome, home_gpt
    # Follow redirects to the canonical host (e.g. apex -> www)
    if home_chrome.final_url and domain_of(home_chrome.final_url) != site.domain:
        site.base = base_of(home_chrome.final_url)
        site.domain = domain_of(home_chrome.final_url)

    site.robots_status, _, site.robots_txt = robots
    if site.robots_status != 200 or "<html" in (site.robots_txt or "")[:500].lower():
        site.robots_txt = ""
    st, ctype, txt = llms
    site.llms_status = st
    site.llms_txt = txt if st == 200 and "<html" not in txt[:500].lower() and len(txt.strip()) > 20 else ""

    site.sitemap_found, site.sitemap_urls = await _load_sitemaps(client, site.base, site.robots_txt)

    # Candidate pages: nav links first (what the owner thinks matters), then sitemap
    if home_chrome.ok:
        site.nav_links = extract_links(home_chrome.html, site.base)
        site.pages[home_chrome.final_url or url] = home_chrome
    def canon(u: str) -> str:
        p = urlparse(u)
        if domain_of(u) == site.domain:
            u = f"{urlparse(site.base).scheme}://{urlparse(site.base).netloc}{p.path}"
        return u.rstrip("/") or u

    candidates: dict[str, list[str]] = {}
    for link in site.nav_links:
        for k in classify(link["href"], link["text"]):
            candidates.setdefault(k, []).append(canon(link["href"]))
    for u in site.sitemap_urls:
        for k in classify(u["loc"]):
            candidates.setdefault(k, []).append(canon(u["loc"]))
    for k, urls in candidates.items():
        site.page_kinds[k] = list(dict.fromkeys(urls))

    # Pick a balanced set of pages to fetch: up to 2 per kind, capped
    picks: list[str] = []
    order = ["about", "team", "services", "areas", "faq", "proof", "contact", "blog"]
    for kind in order:
        for u in site.page_kinds.get(kind, [])[:2]:
            if u not in picks and u.rstrip("/") != (home_chrome.final_url or url).rstrip("/"):
                picks.append(u)
    picks = picks[: max(0, config.MAX_PAGES - 1)]
    results = await asyncio.gather(*(fetch_raw(client, u) for u in picks))
    for p in results:
        if p.ok:
            site.pages[p.final_url or p.url] = p
    return site
