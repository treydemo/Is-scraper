import os
import re
import json
import asyncio
import httpx
from collections import Counter
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlunparse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from audit import run_audit

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

app = FastAPI(title="Immersive Authority & Visibility Audit Engine", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class AuditRequest(BaseModel):
    url: str
    business_name: str = ""
    contact_name: str = ""
    challenge: str = ""
    location: str = ""
    phone: str = ""
    youtube_url: str = ""
    facebook_url: str = ""
    instagram_url: str = ""
    linkedin_url: str = ""

# ── GRADE / LABEL / COLOR ──────────────────────────────────────────────────────
def grade_from_score(s: int) -> str:
    if s >= 90: return "A"
    if s >= 80: return "B"
    if s >= 70: return "C"
    if s >= 60: return "D"
    return "F"

def label_from_score(s: int) -> str:
    if s >= 90: return "Excellent"
    if s >= 80: return "Good"
    if s >= 70: return "Fair"
    if s >= 60: return "Poor"
    return "Critical"

def color_from_score(s: int) -> str:
    if s >= 90: return "#00C2A0"
    if s >= 80: return "#34d399"
    if s >= 70: return "#F5A623"
    if s >= 60: return "#f97316"
    return "#ef4444"

# ── URL NORMALIZER ─────────────────────────────────────────────────────────────
def normalize_url(raw: str) -> str:
    url = re.sub(r'\s+', '', raw.strip())
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    return urlunparse(parsed._replace(scheme="https")).rstrip("/")

# ── SHARED PLAYWRIGHT CONFIG ───────────────────────────────────────────────────
BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ── BRAND COLOR EXTRACTOR ──────────────────────────────────────────────────────
def extract_brand_colors(all_html: str, soup: BeautifulSoup) -> dict:
    hex_re = re.compile(r'#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b')
    css = " ".join(t.get_text() for t in soup.find_all("style"))
    css += " ".join(t.get("style", "") for t in soup.find_all(style=True))
    theme = soup.find("meta", attrs={"name": "theme-color"})
    if theme and theme.get("content"):
        css += " " + theme["content"]
    normalized = []
    for h in hex_re.findall(css):
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        if max(r, g, b) < 30 or min(r, g, b) > 225:
            continue
        if abs(r-g) < 20 and abs(g-b) < 20 and abs(r-b) < 20:
            continue
        normalized.append(f"#{h.upper()}")
    top = [c for c, _ in Counter(normalized).most_common(5)]
    return {
        "primary": top[0] if top else "#1a1a2e",
        "secondary": top[1] if len(top) > 1 else "#00d4ff",
        "palette": top[:5],
    }

# ── LOGO EXTRACTOR ─────────────────────────────────────────────────────────────
def extract_logo(soup: BeautifulSoup, base_url: str):
    def resolve(src):
        if not src: return None
        if src.startswith("http"): return src
        if src.startswith("//"): return "https:" + src
        if src.startswith("/"):
            p = urlparse(base_url)
            return f"{p.scheme}://{p.netloc}{src}"
        return None
    for attr in [
        {"class": re.compile(r"logo", re.I)},
        {"id": re.compile(r"logo", re.I)},
        {"alt": re.compile(r"logo", re.I)},
        {"class": re.compile(r"site-logo|brand-logo|navbar-brand", re.I)},
    ]:
        for img in soup.find_all("img", attrs=attr):
            src = img.get("src") or img.get("data-src")
            r = resolve(src)
            if r: return r
    for tag in ["header", "nav"]:
        sec = soup.find(tag)
        if sec:
            img = sec.find("img")
            if img:
                src = img.get("src") or img.get("data-src")
                r = resolve(src)
                if r: return r
    og = soup.find("meta", property="og:image")
    if og and og.get("content"): return og["content"]
    return None

# ── SCHEMA TYPES ───────────────────────────────────────────────────────────────
def extract_schema_types(soup: BeautifulSoup) -> list:
    types = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            def collect(obj):
                if isinstance(obj, dict):
                    t = obj.get("@type")
                    if isinstance(t, list): types.extend(t)
                    elif t: types.append(t)
                    for v in obj.values(): collect(v)
                elif isinstance(obj, list):
                    for item in obj: collect(item)
            collect(data)
        except Exception:
            pass
    return list(set(types))

# ── STEP 1: WEBSITE SCRAPE (Playwright) ───────────────────────────────────────
EXTRA_PATHS = ["/about", "/about-us", "/team", "/leadership", "/services",
               "/contact", "/mission", "/values", "/faq", "/reviews", "/blog"]

CATEGORY_KEYWORDS = {
    "plumber": ["plumb", "pipe", "drain", "water heater", "leak"],
    "electrician": ["electric", "wiring", "panel", "circuit"],
    "hvac": ["hvac", "heating", "cooling", "air condition", "furnace"],
    "lawyer": ["attorney", "legal", "law firm", "litigation"],
    "dentist": ["dental", "dentist", "teeth", "orthodont"],
    "accountant": ["accounting", "cpa", "tax", "bookkeeping"],
    "roofing": ["roof", "shingle", "gutter"],
    "landscaping": ["landscap", "lawn", "garden", "irrigation"],
    "restaurant": ["restaurant", "menu", "dining", "cuisine"],
    "real estate": ["real estate", "realtor", "homes for sale", "property"],
}

TITLE_RE = r'(?:CEO|CTO|COO|CFO|CMO|Founder|Co-Founder|President|Owner|Director|Principal|Partner|Managing Partner|VP)'

_SCRAPE_BLOCKED = {
    "scrape_status": "blocked",
    "scrape_error": "Site could not be scraped — Cloudflare or bot protection detected",
    "logo_url": None,
    "brand_colors": {"primary": "#1a1a2e", "secondary": "#00d4ff", "palette": []},
    "leadership": [], "mission": None, "vision": None, "values": [],
    "tagline": None, "what_they_say": "Could not scrape site",
    "voice_tone": "unknown", "brand_color_assessment": "", "brand_gap": "",
    "title": None, "meta_description": None, "h1": None, "h2s": [],
    "schema_types_found": [], "has_sitemap": False, "has_robots": False,
    "is_https": False, "internal_links_count": 0, "images_missing_alt": 0,
    "last_modified": None, "last_blog_post_date": None, "total_pages_crawled": 0,
    "body_text_sample": "", "social_urls_discovered": {},
    "detected_category": "",
}

async def scrape_website(url: str) -> dict:
    pages_html = {}
    last_modified = None
    discovered = {"facebook": None, "instagram": None, "linkedin": None, "twitter": None}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
            ctx = await browser.new_context(user_agent=USER_AGENT)
            page = await ctx.new_page()

            # Homepage
            try:
                resp = await page.goto(url, wait_until="networkidle", timeout=30000)
                if resp:
                    last_modified = resp.headers.get("last-modified")
                if resp and resp.status >= 400:
                    await browser.close()
                    return {**_SCRAPE_BLOCKED, "is_https": url.startswith("https")}
                pages_html["home"] = await page.content()
            except Exception:
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    if resp and resp.status >= 400:
                        await browser.close()
                        return {**_SCRAPE_BLOCKED, "is_https": url.startswith("https")}
                    pages_html["home"] = await page.content()
                except Exception:
                    await browser.close()
                    return {**_SCRAPE_BLOCKED, "is_https": url.startswith("https")}

            # Extra paths
            for slug in EXTRA_PATHS:
                try:
                    r = await page.goto(url.rstrip("/") + slug,
                                        wait_until="domcontentloaded", timeout=10000)
                    if r and r.status < 400:
                        pages_html[slug.lstrip("/")] = await page.content()
                except Exception:
                    pass

            await browser.close()
    except Exception:
        return {**_SCRAPE_BLOCKED, "is_https": url.startswith("https")}

    home_html = pages_html.get("home", "")
    all_html = "\n".join(pages_html.values())
    home_soup = BeautifulSoup(home_html, "html.parser")
    full_soup = BeautifulSoup(all_html, "html.parser")

    # SEO basics
    title = (home_soup.title.string or "").strip() if home_soup.title else None
    md = home_soup.find("meta", attrs={"name": "description"})
    meta_desc = md["content"].strip() if md and md.get("content") else None
    h1_tags = [h.get_text(strip=True) for h in full_soup.find_all("h1")]
    h1 = h1_tags[0] if h1_tags else None
    h2s = [h.get_text(strip=True) for h in full_soup.find_all("h2")][:15]

    schema_types = extract_schema_types(full_soup)
    images = full_soup.find_all("img")
    missing_alt = len([i for i in images if not i.get("alt", "").strip()])
    base_domain = urlparse(url).netloc
    internal_links = [a["href"] for a in full_soup.find_all("a", href=True)
                      if base_domain in a["href"] or a["href"].startswith("/")]

    # Social discovery
    for a in full_soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com" in href and not discovered["facebook"]:
            discovered["facebook"] = href
        elif "instagram.com" in href and not discovered["instagram"]:
            discovered["instagram"] = href
        elif "linkedin.com" in href and not discovered["linkedin"]:
            discovered["linkedin"] = href
        elif ("twitter.com" in href or "x.com" in href) and not discovered["twitter"]:
            discovered["twitter"] = href

    brand_colors = extract_brand_colors(all_html, full_soup)
    logo_url = extract_logo(home_soup, url)

    # Body text (strip nav/footer/scripts)
    for tag in full_soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body_text = re.sub(r'\s+', ' ', full_soup.get_text(separator=" ", strip=True))
    body_lower = body_text.lower()

    # Leadership
    leadership = []
    for script in home_soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for key in ["founder", "employee", "member", "author"]:
                entries = data.get(key, [])
                if isinstance(entries, dict): entries = [entries]
                for e in entries:
                    name = e.get("name", "")
                    if name and len(name.split()) >= 2:
                        leadership.append({"name": name.strip(), "title": e.get("jobTitle", "").strip()})
        except Exception:
            pass
    if not leadership:
        for el in full_soup.find_all(["h2", "h3", "h4", "p"]):
            text = el.get_text(strip=True)
            m = re.match(r'^([A-Z][a-z]+ (?:[A-Z][a-z]+ )?[A-Z][a-z]+)[,\-–]\s*(' + TITLE_RE + r'[a-zA-Z\s&]*)', text)
            if m:
                leadership.append({"name": m.group(1).strip(), "title": m.group(2).strip()})
            if len(leadership) >= 4:
                break

    # Mission / vision
    mission = None
    for pat in [r'our mission[:\s]+([^.!?]{20,300}[.!?])', r'we (?:help|exist to|are dedicated to)[^.!?]{10,200}[.!?]']:
        m = re.search(pat, body_text, re.IGNORECASE)
        if m:
            mission = m.group(0).strip()[:300]; break

    vision = None
    m = re.search(r'our vision[:\s]+([^.!?]{20,300}[.!?])', body_text, re.IGNORECASE)
    if m:
        vision = m.group(0).strip()[:300]

    # Values
    values = []
    vs = full_soup.find(string=re.compile(r'our values|core values', re.I))
    if vs and vs.parent:
        sib = vs.parent.find_next_sibling()
        if sib:
            values = [i.get_text(strip=True) for i in sib.find_all(["li", "h3", "h4"])[:6]
                      if len(i.get_text(strip=True)) < 60]

    # Detected category
    detected_category = ""
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in body_lower for kw in kws):
            detected_category = cat; break

    # Blog date
    last_blog_post_date = None
    blog = full_soup.find(["section", "div"], class_=re.compile(r"blog|post|article|news", re.I))
    if blog:
        dm = re.search(r'(\d{4}-\d{2}-\d{2})', blog.get_text())
        if dm: last_blog_post_date = dm.group(1)

    # Sitemap / robots (httpx is fine for direct API calls, not page scraping)
    has_sitemap = False
    has_robots = False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            sm = await client.get(f"{url}/sitemap.xml")
            has_sitemap = sm.status_code == 200
            rb = await client.get(f"{url}/robots.txt")
            has_robots = rb.status_code == 200
    except Exception:
        pass

    return {
        "scrape_status": "ok",
        "scrape_error": None,
        "logo_url": logo_url,
        "brand_colors": brand_colors,
        "leadership": leadership,
        "mission": mission,
        "vision": vision,
        "values": values,
        "tagline": h1[:200] if h1 else None,
        "what_they_say": (title or "")[:200],
        "voice_tone": "professional",
        "brand_color_assessment": "",
        "brand_gap": "",
        "title": title,
        "meta_description": meta_desc,
        "h1": h1,
        "h2s": h2s,
        "schema_types_found": schema_types,
        "has_sitemap": has_sitemap,
        "has_robots": has_robots,
        "is_https": url.startswith("https"),
        "internal_links_count": len(internal_links),
        "images_missing_alt": missing_alt,
        "last_modified": last_modified,
        "last_blog_post_date": last_blog_post_date,
        "total_pages_crawled": len(pages_html),
        "body_text_sample": body_text[:4000],
        "social_urls_discovered": discovered,
        "detected_category": detected_category,
    }

# ── STEP 2: PAGESPEED ──────────────────────────────────────────────────────────
async def get_pagespeed(url: str) -> dict:
    default = {"mobile_score": None, "fcp": None, "lcp": None, "cls": None, "tbt": None}
    if not GOOGLE_API_KEY:
        return default
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                params={"url": url, "strategy": "mobile", "key": GOOGLE_API_KEY}
            )
            if resp.status_code != 200:
                return default
            data = resp.json()
            cats = data.get("lighthouseResult", {}).get("categories", {})
            audits = data.get("lighthouseResult", {}).get("audits", {})
            raw_score = cats.get("performance", {}).get("score")
            return {
                "mobile_score": round(raw_score * 100) if raw_score is not None else None,
                "fcp": audits.get("first-contentful-paint", {}).get("displayValue"),
                "lcp": audits.get("largest-contentful-paint", {}).get("displayValue"),
                "cls": audits.get("cumulative-layout-shift", {}).get("displayValue"),
                "tbt": audits.get("total-blocking-time", {}).get("displayValue"),
            }
    except Exception:
        return default

# ── STEP 3: GBP ───────────────────────────────────────────────────────────────
async def audit_gbp(business_name: str, location: str, phone: str, audit_url: str) -> dict:
    default = {
        "gbp_found": False, "gbp_name": None, "gbp_rating": None,
        "gbp_review_count": None, "gbp_photo_count": None,
        "gbp_has_hours": False, "gbp_website_matches": False,
        "gbp_phone_matches": False, "gbp_status": None,
        "gbp_completeness_gaps": [], "gbp_confidence": "high",
        "gbp_note": None, "gbp_error": None,
    }
    if not GOOGLE_API_KEY or not business_name:
        default["gbp_error"] = "GBP lookup unavailable"
        return default
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            sr = await client.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": f"{business_name} {location}".strip(), "key": GOOGLE_API_KEY}
            )
            if sr.status_code != 200:
                default["gbp_error"] = "GBP lookup unavailable"; return default
            results = sr.json().get("results", [])
            if not results:
                return default

            place = results[0]
            place_id = place.get("place_id", "")
            gbp_name = place.get("name", "")

            dr = await client.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields": "name,rating,user_ratings_total,formatted_address,"
                              "formatted_phone_number,website,opening_hours,photos,"
                              "business_status,types",
                    "key": GOOGLE_API_KEY,
                }
            )
            if dr.status_code != 200:
                default["gbp_error"] = "GBP lookup unavailable"; return default
            d = dr.json().get("result", {})
    except Exception:
        default["gbp_error"] = "GBP lookup unavailable"; return default

    rating = d.get("rating")
    review_count = d.get("user_ratings_total", 0)
    has_hours = bool(d.get("opening_hours", {}).get("weekday_text"))
    photo_count = len(d.get("photos", []))
    gbp_website = d.get("website", "")
    gbp_phone_raw = d.get("formatted_phone_number", "")
    business_status = d.get("business_status", "")

    def domain(u):
        try: return urlparse(u).netloc.replace("www.", "")
        except: return ""
    website_matches = bool(gbp_website and domain(gbp_website) == domain(audit_url))

    def digits(p): return re.sub(r'\D', '', p)
    phone_matches = bool(phone and gbp_phone_raw and digits(phone) == digits(gbp_phone_raw))

    gaps = []
    if not has_hours: gaps.append("Missing business hours")
    if photo_count < 5: gaps.append(f"Low photo count ({photo_count} — aim for 5+)")
    if not gbp_website: gaps.append("No website linked on GBP")
    if (review_count or 0) < 10: gaps.append(f"Low review count ({review_count} — aim for 10+)")
    if rating and rating < 4.0: gaps.append(f"Rating below 4.0 ({rating} stars)")

    confidence = "high"
    note = None
    if gbp_name and business_name:
        # Strip common filler words before comparing
        STOP = {"the", "a", "an", "of", "and", "or", "in", "at", "for", "sit", "dog", "llc", "inc", "co"}
        a_words = set(business_name.lower().split()) - STOP
        b_words = set(gbp_name.lower().split()) - STOP
        overlap = a_words & b_words
        # Require at least one meaningful word in common
        if not overlap:
            confidence = "low"
            note = "GBP listing may not match — please verify your Google Business Profile"

    return {
        "gbp_found": True, "gbp_name": gbp_name,
        "gbp_rating": rating, "gbp_review_count": review_count,
        "gbp_photo_count": photo_count, "gbp_has_hours": has_hours,
        "gbp_website_matches": website_matches, "gbp_phone_matches": phone_matches,
        "gbp_status": business_status, "gbp_completeness_gaps": gaps,
        "gbp_confidence": confidence, "gbp_note": note, "gbp_error": None,
    }

# ── STEP 4: LSA (Playwright SERP) ─────────────────────────────────────────────
async def audit_lsa(business_name: str, location: str) -> dict:
    default = {
        "lsa_detected": False, "lsa_google_guaranteed": False,
        "lsa_google_screened": False, "local_pack_present": False,
        "local_pack_position": None, "lsa_confidence": "not_detected",
        "lsa_note": "LSA status could not be verified — manual check recommended",
    }
    if not business_name:
        return default

    city = location.split(",")[0].strip() if "," in location else location
    queries = [f"{business_name} {location}".strip(), f"{business_name} {city}".strip()]

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
            ctx = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
            page = await ctx.new_page()

            lsa_detected = google_guaranteed = google_screened = False
            local_pack_present = False
            local_pack_position = None

            for query in queries[:2]:
                try:
                    await page.goto(
                        f"https://www.google.com/search?q={query.replace(' ', '+')}",
                        wait_until="domcontentloaded", timeout=15000
                    )
                    await page.wait_for_timeout(2000)
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    text = soup.get_text().lower()

                    if "google guaranteed" in text:
                        google_guaranteed = True; lsa_detected = True
                    if "google screened" in text:
                        google_screened = True; lsa_detected = True
                    if soup.find(class_=re.compile(r"VkpGBb|rllt__details|uMdZh|lu_map|Nv2PK", re.I)):
                        local_pack_present = True
                    if not local_pack_present and "directions" in text and ("open now" in text or "opens" in text):
                        local_pack_present = True
                    if local_pack_present and business_name.lower()[:8] in text:
                        local_pack_position = 1
                except Exception:
                    continue

            await browser.close()

        return {
            "lsa_detected": lsa_detected,
            "lsa_google_guaranteed": google_guaranteed,
            "lsa_google_screened": google_screened,
            "local_pack_present": local_pack_present,
            "local_pack_position": local_pack_position,
            "lsa_confidence": "detected" if lsa_detected else "not_detected",
            "lsa_note": None,
        }
    except Exception:
        return default

# ── STEP 5: YOUTUBE ────────────────────────────────────────────────────────────
async def audit_youtube(business_name: str, youtube_url: str) -> dict:
    default = {
        "yt_found": False, "yt_channel_name": None,
        "yt_subscriber_count": None, "yt_video_count": None,
        "yt_view_count": None, "yt_last_upload_date": None,
        "yt_upload_cadence": None, "yt_has_recent_content": False,
        "yt_confidence": "not_found",
    }
    if not GOOGLE_API_KEY:
        return default

    channel_id = None
    confidence = "confirmed"

    if youtube_url:
        m = re.search(r'youtube\.com/(?:channel/|@|c/|user/)([^/?&\s]+)', youtube_url)
        if m:
            handle = m.group(1)
            if handle.startswith("UC"):
                channel_id = handle
            else:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        r = await client.get(
                            "https://www.googleapis.com/youtube/v3/search",
                            params={"part": "snippet", "q": handle, "type": "channel",
                                    "maxResults": 1, "key": GOOGLE_API_KEY}
                        )
                        if r.status_code == 200:
                            items = r.json().get("items", [])
                            if items: channel_id = items[0]["snippet"]["channelId"]
                except Exception:
                    pass

    if not channel_id and business_name:
        confidence = "estimated"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "q": business_name, "type": "channel",
                            "maxResults": 3, "key": GOOGLE_API_KEY}
                )
                if r.status_code == 200:
                    items = r.json().get("items", [])
                    if items: channel_id = items[0]["snippet"]["channelId"]
        except Exception:
            return default

    if not channel_id:
        return default

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            sr = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics,snippet", "id": channel_id, "key": GOOGLE_API_KEY}
            )
            if sr.status_code != 200: return default
            items = sr.json().get("items", [])
            if not items: return default
            ch = items[0]
            stats = ch.get("statistics", {})
            channel_name = ch.get("snippet", {}).get("title", "")
            subscriber_count = int(stats.get("subscriberCount", 0) or 0)
            video_count = int(stats.get("videoCount", 0) or 0)
            view_count = int(stats.get("viewCount", 0) or 0)

            vr = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "channelId": channel_id, "order": "date",
                        "maxResults": 10, "key": GOOGLE_API_KEY}
            )
            last_upload_date = None
            upload_cadence = None
            has_recent = False
            if vr.status_code == 200:
                vitems = vr.json().get("items", [])
                dates = []
                for v in vitems:
                    pub = v.get("snippet", {}).get("publishedAt", "")
                    if pub:
                        try: dates.append(datetime.fromisoformat(pub.replace("Z", "+00:00")))
                        except Exception: pass
                if dates:
                    dates.sort(reverse=True)
                    last_upload_date = dates[0].strftime("%Y-%m-%d")
                    has_recent = (datetime.now(timezone.utc) - dates[0]).days <= 90
                    if len(dates) >= 2:
                        span = max((dates[0] - dates[-1]).days, 1)
                        upload_cadence = round((len(dates) / span) * 30, 1)

        return {
            "yt_found": True, "yt_channel_name": channel_name,
            "yt_subscriber_count": subscriber_count, "yt_video_count": video_count,
            "yt_view_count": view_count, "yt_last_upload_date": last_upload_date,
            "yt_upload_cadence": upload_cadence, "yt_has_recent_content": has_recent,
            "yt_confidence": confidence,
        }
    except Exception:
        return default

# ── STEP 6: SOCIAL (Playwright) ───────────────────────────────────────────────
async def audit_social(discovered: dict, fb_url: str, ig_url: str, li_url: str) -> dict:
    _p = {"found": False, "url": None, "followers": None}
    default = {
        "platforms_discovered": [],
        "facebook": {**_p}, "instagram": {**_p}, "linkedin": {**_p},
        "total_social_reach": 0, "last_active_platform": None,
        "days_since_last_post": None, "social_is_active": False,
    }
    to_check = {}
    if fb_url or discovered.get("facebook"): to_check["facebook"] = fb_url or discovered["facebook"]
    if ig_url or discovered.get("instagram"): to_check["instagram"] = ig_url or discovered["instagram"]
    if li_url or discovered.get("linkedin"): to_check["linkedin"] = li_url or discovered["linkedin"]
    if not to_check:
        return default

    facebook_data = {**_p}
    instagram_data = {**_p}
    linkedin_data = {**_p}
    total_reach = 0
    platforms_found = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
            ctx = await browser.new_context(user_agent=USER_AGENT)

            for platform, purl in to_check.items():
                pg = await ctx.new_page()
                try:
                    await pg.goto(purl, wait_until="domcontentloaded", timeout=15000)
                    await pg.wait_for_timeout(1500)
                    text = BeautifulSoup(await pg.content(), "html.parser").get_text()
                    followers = None

                    if platform == "facebook":
                        m = re.search(r'([\d,]+)\s*(?:people follow|followers|likes)', text, re.IGNORECASE)
                        if m: followers = int(m.group(1).replace(",", ""))
                        lp = None
                        m2 = re.search(r'(\d+)\s*(hour|day|minute|week)', text, re.IGNORECASE)
                        if m2:
                            val, unit = int(m2.group(1)), m2.group(2).lower()
                            if "minute" in unit or "hour" in unit:
                                lp = datetime.now().strftime("%Y-%m-%d")
                            elif "day" in unit:
                                lp = (datetime.now() - timedelta(days=val)).strftime("%Y-%m-%d")
                        facebook_data = {"found": True, "url": purl, "followers": followers, "last_post": lp, "verified": False}

                    elif platform == "instagram":
                        m = re.search(r'([\d,.]+[KMk]?)\s*[Ff]ollowers', text)
                        if m:
                            raw = m.group(1).replace(",", "").upper()
                            try:
                                if "K" in raw: followers = int(float(raw.replace("K","")) * 1000)
                                elif "M" in raw: followers = int(float(raw.replace("M","")) * 1_000_000)
                                else: followers = int(float(raw))
                            except Exception: pass
                        pm = re.search(r'([\d,]+)\s*posts', text, re.IGNORECASE)
                        post_count = int(pm.group(1).replace(",", "")) if pm else None
                        instagram_data = {"found": True, "url": purl, "followers": followers, "post_count": post_count}

                    elif platform == "linkedin":
                        m = re.search(r'([\d,]+)\s*followers', text, re.IGNORECASE)
                        if m: followers = int(m.group(1).replace(",", ""))
                        em = re.search(r'([\d,\-]+)\s*employees', text, re.IGNORECASE)
                        linkedin_data = {"found": True, "url": purl, "followers": followers,
                                         "employees": em.group(1) if em else None}

                    if followers: total_reach += followers
                    platforms_found.append(platform)

                except Exception:
                    data_blocked = {"found": True, "url": purl, "followers": None,
                                    "data_status": "profile_private_or_blocked"}
                    if platform == "facebook": facebook_data = data_blocked
                    elif platform == "instagram": instagram_data = data_blocked
                    elif platform == "linkedin": linkedin_data = data_blocked
                    platforms_found.append(platform)
                finally:
                    await pg.close()

            await browser.close()
    except Exception:
        pass

    return {
        "platforms_discovered": platforms_found,
        "facebook": facebook_data, "instagram": instagram_data, "linkedin": linkedin_data,
        "total_social_reach": total_reach,
        "last_active_platform": platforms_found[-1] if platforms_found else None,
        "days_since_last_post": None,
        "social_is_active": len(platforms_found) > 0,
    }

# ── STEP 7: AI CITATION CHECK ─────────────────────────────────────────────────
async def check_ai_citation(business_name: str, location: str) -> dict:
    default = {
        "ai_citation_result": "",
        "ai_citation_status": "error",
        "ai_citation_summary": f"AI citation check failed for {business_name}",
    }
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            default["ai_citation_summary"] = "ANTHROPIC_API_KEY not configured"
            return default

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system="You are answering a question about a local business. Answer naturally and honestly based only on what you actually know. If you don't have information about this specific business, say so clearly.",
            messages=[{"role": "user", "content":
                f"Tell me about {business_name} in {location}. What do you know about this business, "
                f"what services do they offer, and why should someone choose them?"}]
        )
        result_text = response.content[0].text.strip()
        tl = result_text.lower()
        biz_lower = business_name.lower()

        has_no_info = any(phrase in tl for phrase in [
            "i don't have", "i do not have", "no information", "i'm not aware",
            "i cannot find", "i don't know", "i have no", "no specific information",
            "not familiar", "i lack", "i couldn't find",
        ])
        is_generic = any(phrase in tl for phrase in [
            "they likely offer", "typically offer", "most businesses like",
            "i would expect", "generally speaking",
        ])
        has_name = (biz_lower[:8] in tl) if len(biz_lower) >= 4 else False

        if has_no_info or not has_name:
            status = "invisible"
            summary = (f"When potential customers ask AI assistants about {business_name}, "
                       f"they get no answer — your business is invisible to AI-powered search.")
        elif is_generic:
            status = "weak"
            summary = (f"AI assistants give only vague, generic information about {business_name} "
                       f"— not enough to drive customer confidence.")
        else:
            status = "strong"
            summary = (f"AI assistants have solid knowledge of {business_name} "
                       f"and can describe their services specifically.")

        return {"ai_citation_result": result_text, "ai_citation_status": status,
                "ai_citation_summary": summary}
    except Exception as e:
        default["ai_citation_summary"] = f"AI citation check failed: {str(e)}"
        return default

# ── STEP 8: COMPETITOR DETECTION (Playwright SERP) ────────────────────────────
SKIP_DOMAINS = {
    "yelp.com", "bbb.org", "angi.com", "homeadvisor.com", "thumbtack.com",
    "facebook.com", "yellowpages.com", "angieslist.com", "houzz.com",
    "google.com", "nextdoor.com", "foursquare.com", "bark.com",
}

async def audit_competitors(business_name: str, location: str,
                            detected_category: str, audit_url: str) -> list:
    if not business_name or not location:
        return []
    city = location.split(",")[0].strip()
    state = location.split(",")[1].strip() if "," in location else ""
    category = detected_category or "local business"
    query = f"{category} {city} {state}".strip()
    audit_domain = urlparse(audit_url).netloc.replace("www.", "")
    competitors = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
            ctx = await browser.new_context(user_agent=USER_AGENT, locale="en-US")
            page = await ctx.new_page()
            try:
                await page.goto(
                    f"https://www.google.com/search?q={query.replace(' ', '+')}",
                    wait_until="domcontentloaded", timeout=15000
                )
                await page.wait_for_timeout(2000)
                soup = BeautifulSoup(await page.content(), "html.parser")
                seen_domains = set()

                for result in soup.find_all(["div", "a"], href=re.compile(r'^https?://')):
                    href = result.get("href", "")
                    if not href.startswith("http"): continue
                    parsed = urlparse(href)
                    dom = parsed.netloc.replace("www.", "")
                    if not dom or dom == audit_domain: continue
                    if any(skip in dom for skip in SKIP_DOMAINS): continue
                    if dom in seen_domains: continue
                    seen_domains.add(dom)

                    txt = result.get_text()
                    name_el = result.find_previous(["h3", "h2"])
                    name = name_el.get_text(strip=True) if name_el else dom.split(".")[0].title()
                    rm = re.search(r'(\d\.\d)\s*(?:stars?|\()', txt)
                    rev = re.search(r'([\d,]+)\s*(?:reviews?|ratings?)', txt, re.IGNORECASE)
                    has_lsa = "google guaranteed" in txt.lower() or "google screened" in txt.lower()

                    competitors.append({
                        "competitor_name": name[:60],
                        "competitor_url": f"{parsed.scheme}://{parsed.netloc}",
                        "competitor_gbp_rating": float(rm.group(1)) if rm else None,
                        "competitor_gbp_reviews": int(rev.group(1).replace(",","")) if rev else None,
                        "competitor_has_lsa": has_lsa,
                        "competitor_local_pack_position": None,
                    })
                    if len(competitors) >= 3: break
            except Exception:
                pass
            await browser.close()
    except Exception:
        pass

    return competitors

# ── STEP 9: SCHEMA GAP ANALYSIS ───────────────────────────────────────────────
SCHEMA_CHECK = ["LocalBusiness", "FAQPage", "Service", "Review", "AggregateRating",
                "BreadcrumbList", "Organization", "WebSite", "Person",
                "VideoObject", "HowTo", "Article"]

LB_SUBTYPES = ["Plumber", "LegalService", "MedicalBusiness", "Electrician", "Dentist",
               "Accountant", "Restaurant", "RealEstateAgent", "HomeAndConstructionBusiness"]

def compute_schema_audit(schema_types_found: list) -> dict:
    present = schema_types_found or []
    has_lb = any(t in present for t in ["LocalBusiness"] + LB_SUBTYPES)
    schema_present = [s for s in SCHEMA_CHECK if s in present]
    if has_lb and "LocalBusiness" not in schema_present:
        schema_present.insert(0, "LocalBusiness")
    schema_missing = [s for s in SCHEMA_CHECK if s not in present]
    if has_lb and "LocalBusiness" in schema_missing:
        schema_missing.remove("LocalBusiness")
    score = min(100, round(len(schema_present) / len(SCHEMA_CHECK) * 100))

    if not has_lb:
        priority = "LocalBusiness schema — required for local search visibility and AI citation"
    elif "FAQPage" not in present:
        priority = "FAQPage schema — high AI citation and featured snippet value"
    elif "AggregateRating" not in present:
        priority = "AggregateRating schema — displays star ratings in search results"
    elif "Service" not in present:
        priority = "Service schema — tells search engines exactly what you offer"
    else:
        priority = schema_missing[0] if schema_missing else "Schema coverage is strong"

    return {"schema_present": schema_present, "schema_missing": schema_missing,
            "schema_score": score, "schema_priority_fix": priority}

# ── STEP 10: SCORING ──────────────────────────────────────────────────────────
def compute_seo_trust_score(schema_score, mobile_score, is_https, has_sitemap,
                            has_robots, title, meta_desc, h1, last_modified) -> int:
    s = 0
    s += round((schema_score / 100) * 25)           # schema: 25 pts
    if mobile_score is not None:
        s += round((mobile_score / 100) * 25)        # pagespeed: 25 pts
    if is_https: s += 8                              # https: 8 pts
    if has_sitemap: s += 8                           # sitemap: 8 pts
    if has_robots: s += 8                            # robots: 8 pts
    if title: s += 5                                 # meta completeness: 15 pts
    if meta_desc: s += 5
    if h1: s += 5
    if last_modified:                                # freshness: 11 pts
        try:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(last_modified)
            days = (datetime.now(timezone.utc) - d).days
            if days < 90: s += 11
            elif days < 180: s += 5
        except Exception:
            pass
    return min(100, s)

def compute_ai_visibility_score(gbp_found, gbp_rating, gbp_review_count,
                                 lsa_detected, lsa_guaranteed,
                                 platforms, ai_status, schema_score) -> int:
    s = 0
    if gbp_found:                                    # GBP: 30 pts
        s += 10
        if gbp_rating and gbp_rating >= 4.0: s += 10
        if gbp_review_count and gbp_review_count >= 10: s += 10
    if lsa_guaranteed: s += 20                       # LSA: 20 pts
    elif lsa_detected: s += 10
    n = len(platforms)                               # social: 20 pts
    if n >= 2: s += 20
    elif n == 1: s += 10
    if ai_status == "strong": s += 20               # AI citation: 20 pts
    elif ai_status == "weak": s += 10
    if schema_score >= 50: s += 10                  # schema parseable: 10 pts
    elif schema_score >= 25: s += 5
    return min(100, s)

def compute_freshness(last_modified, last_blog) -> str:
    date_str = last_modified or last_blog
    if not date_str: return "unknown"
    try:
        if last_modified:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(last_modified)
        else:
            d = datetime.fromisoformat(last_blog)
            if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - d).days
        if days < 90: return "fresh"
        if days < 180: return "aging"
        return "stale"
    except Exception:
        return "unknown"

# ── MAIN AUDIT RUNNER ─────────────────────────────────────────────────────────
_WEB_FAIL = {**_SCRAPE_BLOCKED}
_GBP_FAIL = {"gbp_found": False, "gbp_name": None, "gbp_rating": None, "gbp_review_count": None,
             "gbp_photo_count": None, "gbp_has_hours": False, "gbp_website_matches": False,
             "gbp_phone_matches": False, "gbp_status": None, "gbp_completeness_gaps": [],
             "gbp_confidence": "high", "gbp_note": None, "gbp_error": "GBP lookup unavailable"}
_PS_FAIL = {"mobile_score": None, "fcp": None, "lcp": None, "cls": None, "tbt": None}
_YT_FAIL = {"yt_found": False, "yt_channel_name": None, "yt_subscriber_count": None,
            "yt_video_count": None, "yt_view_count": None, "yt_last_upload_date": None,
            "yt_upload_cadence": None, "yt_has_recent_content": False, "yt_confidence": "not_found"}

async def run_full_audit(request: AuditRequest, clean_url: str) -> dict:
    def safe(r, fallback):
        return r if not isinstance(r, Exception) else fallback

    # Phase 1: concurrent
    r1, r2, r3, r4 = await asyncio.gather(
        scrape_website(clean_url),
        get_pagespeed(clean_url),
        audit_gbp(request.business_name, request.location, request.phone, clean_url),
        audit_youtube(request.business_name, request.youtube_url),
        return_exceptions=True,
    )
    website = safe(r1, {**_WEB_FAIL, "is_https": clean_url.startswith("https")})
    pagespeed = safe(r2, _PS_FAIL)
    gbp = safe(r3, _GBP_FAIL)
    youtube = safe(r4, _YT_FAIL)

    # Phase 2: sequential
    lsa = await audit_lsa(request.business_name, request.location)
    competitors = await audit_competitors(
        request.business_name, request.location,
        website.get("detected_category", ""), clean_url
    )
    social = await audit_social(
        website.get("social_urls_discovered", {}),
        request.facebook_url, request.instagram_url, request.linkedin_url
    )
    ai_citation = await check_ai_citation(request.business_name, request.location)

    # Derived data
    schema_audit = compute_schema_audit(website.get("schema_types_found", []))
    seo_score = compute_seo_trust_score(
        schema_audit["schema_score"],
        pagespeed.get("mobile_score"),
        website.get("is_https", False),
        website.get("has_sitemap", False),
        website.get("has_robots", False),
        website.get("title"),
        website.get("meta_description"),
        website.get("h1"),
        website.get("last_modified"),
    )
    ai_score = compute_ai_visibility_score(
        gbp.get("gbp_found", False), gbp.get("gbp_rating"), gbp.get("gbp_review_count"),
        lsa.get("lsa_detected", False), lsa.get("lsa_google_guaranteed", False),
        social.get("platforms_discovered", []),
        ai_citation.get("ai_citation_status", "invisible"),
        schema_audit["schema_score"],
    )
    freshness = compute_freshness(website.get("last_modified"), website.get("last_blog_post_date"))

    # Phase 3: Claude final audit
    report = await run_audit(
        website_data=website, pagespeed_data=pagespeed, gbp_data=gbp,
        lsa_data=lsa, youtube_data=youtube, social_data=social,
        ai_citation_data=ai_citation, schema_data=schema_audit,
        competitors=competitors, business_name=request.business_name,
        contact_name=request.contact_name, challenge=request.challenge,
        location=request.location, seo_score=seo_score, ai_score=ai_score,
    )

    bi = report.get("brand_intelligence", {})

    return {
        "status": "success",
        "version": "4.0.0",
        "audit_type": "full",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "business_name": request.business_name,
        "url_analyzed": clean_url,
        "contact_name": request.contact_name,
        "location": request.location,

        "brand_intelligence": {
            "logo_url": website.get("logo_url"),
            "brand_colors": website.get("brand_colors", {"primary": "#1a1a2e", "secondary": "#00d4ff", "palette": []}),
            "leadership": website.get("leadership", []),
            "mission": website.get("mission"),
            "vision": website.get("vision"),
            "values": website.get("values", []),
            "tagline": website.get("tagline"),
            "what_they_say": bi.get("what_they_say") or website.get("what_they_say", ""),
            "voice_tone": bi.get("voice_tone") or website.get("voice_tone", "unknown"),
            "brand_color_assessment": bi.get("brand_color_assessment", ""),
            "brand_gap": bi.get("brand_gap", ""),
        },

        "seo_trust_score": seo_score,
        "seo_trust_grade": grade_from_score(seo_score),
        "seo_trust_label": label_from_score(seo_score),
        "seo_trust_color": color_from_score(seo_score),

        "ai_visibility_score": ai_score,
        "ai_visibility_grade": grade_from_score(ai_score),
        "ai_visibility_label": label_from_score(ai_score),
        "ai_visibility_color": color_from_score(ai_score),

        "ai_citation": {
            "ai_citation_result": ai_citation.get("ai_citation_result", ""),
            "ai_citation_status": ai_citation.get("ai_citation_status", "error"),
            "ai_citation_summary": ai_citation.get("ai_citation_summary", ""),
        },

        "pagespeed": {
            "mobile_score": pagespeed.get("mobile_score"),
            "fcp": pagespeed.get("fcp"),
            "lcp": pagespeed.get("lcp"),
            "cls": pagespeed.get("cls"),
            "tbt": pagespeed.get("tbt"),
        },

        "gbp": {
            "gbp_found": gbp.get("gbp_found", False),
            "gbp_name": gbp.get("gbp_name"),
            "gbp_rating": gbp.get("gbp_rating"),
            "gbp_review_count": gbp.get("gbp_review_count"),
            "gbp_photo_count": gbp.get("gbp_photo_count"),
            "gbp_has_hours": gbp.get("gbp_has_hours", False),
            "gbp_website_matches": gbp.get("gbp_website_matches", False),
            "gbp_completeness_gaps": gbp.get("gbp_completeness_gaps", []),
            "gbp_confidence": gbp.get("gbp_confidence", "high"),
            "gbp_note": gbp.get("gbp_note"),
        },

        "lsa": {
            "lsa_detected": lsa.get("lsa_detected", False),
            "lsa_google_guaranteed": lsa.get("lsa_google_guaranteed", False),
            "lsa_google_screened": lsa.get("lsa_google_screened", False),
            "local_pack_present": lsa.get("local_pack_present", False),
            "local_pack_position": lsa.get("local_pack_position"),
            "lsa_confidence": lsa.get("lsa_confidence", "not_detected"),
            "lsa_note": lsa.get("lsa_note"),
        },

        "youtube": {
            "yt_found": youtube.get("yt_found", False),
            "yt_channel_name": youtube.get("yt_channel_name"),
            "yt_subscriber_count": youtube.get("yt_subscriber_count"),
            "yt_video_count": youtube.get("yt_video_count"),
            "yt_last_upload_date": youtube.get("yt_last_upload_date"),
            "yt_has_recent_content": youtube.get("yt_has_recent_content", False),
            "yt_confidence": youtube.get("yt_confidence", "not_found"),
        },

        "social": {
            "platforms_discovered": social.get("platforms_discovered", []),
            "facebook": social.get("facebook", {"found": False, "url": None, "followers": None}),
            "instagram": social.get("instagram", {"found": False, "url": None, "followers": None}),
            "linkedin": social.get("linkedin", {"found": False, "url": None, "followers": None}),
            "total_social_reach": social.get("total_social_reach", 0),
            "social_is_active": social.get("social_is_active", False),
            "days_since_last_post": social.get("days_since_last_post"),
        },

        "schema_audit": schema_audit,

        "content_freshness": {
            "last_modified": website.get("last_modified"),
            "last_blog_post_date": website.get("last_blog_post_date"),
            "total_pages_crawled": website.get("total_pages_crawled", 0),
            "freshness_status": freshness,
        },

        "competitors": competitors,

        "report": {
            "executive_summary": report.get("executive_summary", ""),
            "channel_scores": report.get("channel_scores", {}),
            "top_seo_gaps": report.get("top_seo_gaps", []),
            "top_visibility_gaps": report.get("top_visibility_gaps", []),
            "quick_wins": report.get("quick_wins", []),
            "recommended_next_steps": report.get("recommended_next_steps", []),
            "ia_pitch": report.get("ia_pitch", ""),
        },
    }

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "Immersive Authority & Visibility Audit Engine v4.0 is live", "version": "4.0.0"}

@app.post("/audit")
async def audit_endpoint(request: AuditRequest):
    return await run_full_audit(request, normalize_url(request.url))

@app.post("/audit-pdf")
async def audit_pdf_endpoint(request: AuditRequest):
    from pdf_generator import generate_pdf_base64
    clean_url = normalize_url(request.url)
    audit_data = await run_full_audit(request, clean_url)
    pdf_b64 = generate_pdf_base64(audit_data)
    return {
        **audit_data,
        "pdf_base64": pdf_b64,
        "pdf_filename": f"IA-Audit-{request.business_name.replace(' ', '-')}-v4.pdf",
    }
