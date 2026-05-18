import os
import re
import json
import asyncio
import httpx
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from audit import run_audit

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

app = FastAPI(title="IA Immersive Authority & Visibility Audit Engine", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# ─────────────────────────────────────────
# SCORE HELPERS
# ─────────────────────────────────────────
def score_color(s: int) -> str:
    return "#22c55e" if s > 65 else ("#f59e0b" if s > 40 else "#ef4444")

def score_label(s: int) -> str:
    return "Strong" if s > 65 else ("Moderate" if s > 40 else "Critical")

# ─────────────────────────────────────────
# URL NORMALIZER
# ─────────────────────────────────────────
def normalize_url(raw: str) -> str:
    url = raw.strip().lower()
    url = re.sub(r'\s+', '', url)
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    normalized = urlunparse(parsed._replace(scheme="https"))
    return normalized.rstrip("/")

# ─────────────────────────────────────────
# BRAND COLOR EXTRACTOR
# ─────────────────────────────────────────
def extract_brand_colors(html: str, soup: BeautifulSoup) -> dict:
    hex_pattern = re.compile(r'#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b')
    style_tags = soup.find_all("style")
    inline_styles = [tag.get("style", "") for tag in soup.find_all(style=True)]
    all_css = " ".join([s.get_text() for s in style_tags]) + " ".join(inline_styles)

    hex_colors = hex_pattern.findall(all_css)
    normalized_hex = []
    for h in hex_colors:
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        if max(r, g, b) < 30 or min(r, g, b) > 225:
            continue
        if abs(r-g) < 20 and abs(g-b) < 20 and abs(r-b) < 20:
            continue
        normalized_hex.append(f"#{h.upper()}")

    color_counts = Counter(normalized_hex)
    top_colors = [c for c, _ in color_counts.most_common(5)]
    return {
        "primary": top_colors[0] if top_colors else "#1a1a2e",
        "secondary": top_colors[1] if len(top_colors) > 1 else "#00d4ff",
        "palette": top_colors[:5]
    }

# ─────────────────────────────────────────
# LOGO EXTRACTOR
# ─────────────────────────────────────────
def extract_logo(soup: BeautifulSoup, base_url: str) -> str:
    def resolve(src):
        if not src:
            return ""
        if src.startswith("http"):
            return src
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("/"):
            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{src}"
        return ""

    for attr in [
        {"class": re.compile(r"logo", re.I)},
        {"id": re.compile(r"logo", re.I)},
        {"alt": re.compile(r"logo", re.I)},
        {"class": re.compile(r"site-logo|brand-logo|navbar-brand", re.I)},
    ]:
        for c in soup.find_all("img", attrs=attr):
            src = c.get("src", "") or c.get("data-src", "")
            resolved = resolve(src)
            if resolved:
                return resolved

    for header_tag in ["header", "nav"]:
        section = soup.find(header_tag)
        if section:
            img = section.find("img")
            if img:
                src = img.get("src", "") or img.get("data-src", "")
                resolved = resolve(src)
                if resolved:
                    return resolved

    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        return og_image["content"]
    return ""

# ─────────────────────────────────────────
# BRAND IDENTITY EXTRACTOR
# ─────────────────────────────────────────
def extract_brand_identity(soup: BeautifulSoup, body_text: str) -> dict:
    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    tagline = h1s[0][:200] if h1s else ""

    mission = ""
    for pattern in [
        r'our mission[:\s]+([^.!?]{20,200}[.!?])',
        r'mission[:\s]+([^.!?]{20,200}[.!?])',
        r'we (help|exist to|are dedicated to|believe)[^.!?]{10,200}[.!?]',
    ]:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            mission = match.group(0).strip()[:300]
            break

    leadership = []
    titles = r'(?:CEO|CTO|COO|CFO|CMO|Founder|Co-Founder|President|Owner|Director|Principal|Partner|Managing)'

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for key in ["founder", "employee", "member", "author"]:
                entries = data.get(key, [])
                if isinstance(entries, dict):
                    entries = [entries]
                for e in entries:
                    name = e.get("name", "")
                    if name and len(name.split()) >= 2:
                        entry = name.strip()
                        role = e.get("jobTitle", "")
                        if role:
                            entry += f" ({role})"
                        if entry not in leadership:
                            leadership.append(entry)
        except Exception:
            pass

    if not leadership:
        for el in soup.find_all(["h2", "h3", "h4", "p", "span", "div"]):
            text = el.get_text(strip=True)
            match = re.match(
                r'^([A-Z][a-z]+ (?:[A-Z][a-z]+ )?[A-Z][a-z]+)[,\-–]\s*(' + titles + r'[a-zA-Z\s&]*)',
                text
            )
            if match:
                entry = f"{match.group(1)} ({match.group(2).strip()})"
                if entry not in leadership:
                    leadership.append(entry)
            if len(leadership) >= 4:
                break

    if not leadership:
        for pattern in [
            r'([A-Z][a-z]+ [A-Z][a-z]+)[,\s]+(' + titles + r')',
            r'(' + titles + r')[,\s]+([A-Z][a-z]+ [A-Z][a-z]+)',
        ]:
            for m in re.findall(pattern, body_text)[:3]:
                name = m[0] if re.match(r'[A-Z][a-z]+', m[0]) else m[1]
                role = m[1] if re.match(r'[A-Z][a-z]+', m[0]) else m[0]
                entry = f"{name} ({role})"
                if entry not in leadership:
                    leadership.append(entry)

    founded = ""
    year_match = re.search(r'(?:founded|established|since|started)[^0-9]*(\d{4})', body_text, re.IGNORECASE)
    if year_match:
        year = int(year_match.group(1))
        if 1900 < year < 2030:
            founded = str(year)

    location = ""
    for pattern in [
        r'(?:located|based|headquartered)[^.]*(?:in|at)\s+([A-Z][a-zA-Z\s,]+(?:TX|CA|NY|FL|IL|WA|GA|NC|OH|PA|AZ|CO|MA|VA|TN|MI|MN))',
        r'([A-Z][a-zA-Z]+,\s*(?:TX|CA|NY|FL|IL|WA|GA|NC|OH|PA|AZ|CO))',
    ]:
        match = re.search(pattern, body_text, re.IGNORECASE)
        if match:
            location = match.group(1).strip()[:100]
            break

    return {
        "tagline": tagline,
        "mission": mission,
        "leadership": leadership,
        "founded": founded,
        "location": location,
    }

# ─────────────────────────────────────────
# PAGESPEED INSIGHTS
# ─────────────────────────────────────────
async def get_pagespeed(url: str) -> dict:
    result = {"mobile_score": None, "desktop_score": None, "core_web_vitals": {}}
    if not GOOGLE_API_KEY:
        return result

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                params={"url": url, "strategy": "mobile", "key": GOOGLE_API_KEY}
            )
            if resp.status_code == 200:
                data = resp.json()
                cats = data.get("lighthouseResult", {}).get("categories", {})
                result["mobile_score"] = round((cats.get("performance", {}).get("score") or 0) * 100)
                audits = data.get("lighthouseResult", {}).get("audits", {})
                result["core_web_vitals"] = {
                    "lcp": audits.get("largest-contentful-paint", {}).get("displayValue", ""),
                    "tbt": audits.get("total-blocking-time", {}).get("displayValue", ""),
                    "cls": audits.get("cumulative-layout-shift", {}).get("displayValue", ""),
                    "fcp": audits.get("first-contentful-paint", {}).get("displayValue", ""),
                    "si": audits.get("speed-index", {}).get("displayValue", ""),
                }
        except Exception:
            pass

        try:
            resp = await client.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                params={"url": url, "strategy": "desktop", "key": GOOGLE_API_KEY}
            )
            if resp.status_code == 200:
                data = resp.json()
                cats = data.get("lighthouseResult", {}).get("categories", {})
                result["desktop_score"] = round((cats.get("performance", {}).get("score") or 0) * 100)
        except Exception:
            pass

    return result

# ─────────────────────────────────────────
# CHANNEL 1: WEBSITE (Playwright)
# ─────────────────────────────────────────
async def scrape_website(url: str) -> dict:
    pages_html = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            pages_html["home"] = await page.content()

            for slug in ["/about", "/contact", "/services", "/reviews"]:
                try:
                    resp = await page.goto(url.rstrip("/") + slug, wait_until="domcontentloaded", timeout=10000)
                    if resp and resp.status < 400:
                        pages_html[slug.lstrip("/")] = await page.content()
                except Exception:
                    pass
        finally:
            await browser.close()

    home_html = pages_html.get("home", "")
    all_html = "\n".join(pages_html.values())

    home_soup = BeautifulSoup(home_html, "html.parser")
    full_soup = BeautifulSoup(all_html, "html.parser")

    title = home_soup.title.string.strip() if home_soup.title else ""
    meta_desc_tag = home_soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag else ""
    meta_keywords_tag = home_soup.find("meta", attrs={"name": "keywords"})
    meta_keywords = meta_keywords_tag["content"].strip() if meta_keywords_tag else ""

    og_title, og_desc = "", ""
    og_tag = home_soup.find("meta", property="og:title")
    if og_tag:
        og_title = og_tag.get("content", "")
    og_dtag = home_soup.find("meta", property="og:description")
    if og_dtag:
        og_desc = og_dtag.get("content", "")

    h1s = [h.get_text(strip=True) for h in full_soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in full_soup.find_all("h2")][:15]
    h3s = [h.get_text(strip=True) for h in full_soup.find_all("h3")][:15]

    brand_colors = extract_brand_colors(all_html, full_soup)
    logo_url = extract_logo(home_soup, url)

    # Auto-discover social links from homepage
    social_urls_found = {"facebook": "", "instagram": "", "linkedin": "", "twitter": ""}
    for a in home_soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com" in href and not social_urls_found["facebook"]:
            social_urls_found["facebook"] = href
        elif "instagram.com" in href and not social_urls_found["instagram"]:
            social_urls_found["instagram"] = href
        elif "linkedin.com" in href and not social_urls_found["linkedin"]:
            social_urls_found["linkedin"] = href
        elif ("twitter.com" in href or "x.com" in href) and not social_urls_found["twitter"]:
            social_urls_found["twitter"] = href

    for tag_name in ["script", "style", "nav", "footer", "header"]:
        for tag in full_soup.find_all(tag_name):
            tag.decompose()
    body_text = full_soup.get_text(separator=" ", strip=True)
    body_text = re.sub(r'\s+', ' ', body_text)[:8000]

    brand_identity = extract_brand_identity(home_soup, body_text)

    images = full_soup.find_all("img")
    total_images = len(images)
    images_missing_alt = len([i for i in images if not i.get("alt", "").strip()])

    base_domain = urlparse(url).netloc
    all_links = home_soup.find_all("a", href=True)
    internal_links = [a["href"] for a in all_links if base_domain in a["href"] or a["href"].startswith("/")]
    external_links = [a["href"] for a in all_links if a["href"].startswith("http") and base_domain not in a["href"]]

    schema_tags = full_soup.find_all("script", type="application/ld+json")
    schema_found = []
    for tag in schema_tags:
        try:
            data = json.loads(tag.string)
            schema_type = data.get("@type", "Unknown") if isinstance(data, dict) else "Multiple"
            schema_found.append(schema_type)
        except Exception:
            pass

    text_lower = body_text.lower()
    faq_signals = {
        "has_faq_section": bool(
            full_soup.find(id=re.compile("faq", re.I)) or
            full_soup.find(class_=re.compile("faq", re.I))
        ),
        "question_headers": len([h for h in h2s + h3s if any(
            q in h.lower() for q in ["how", "what", "why", "when", "where", "who", "can", "do ", "is "]
        )]),
        "has_faq_schema": any("faqpage" in s.lower() for s in schema_found),
    }

    phone_numbers = list(set(re.findall(r'(\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4})', body_text)))
    address_signals = any(word in text_lower for word in ["street", "ave", "blvd", "suite", "ste.", " tx ", " texas "])

    has_sitemap = False
    has_robots = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            sm = await client.get(f"{url}/sitemap.xml")
            has_sitemap = sm.status_code == 200
            rb = await client.get(f"{url}/robots.txt")
            has_robots = rb.status_code == 200
    except Exception:
        pass

    is_https = url.startswith("https")

    # Score
    score = sum([
        10 if is_https else 0,
        10 if meta_desc else 0,
        15 if schema_found else 0,
        10 if (faq_signals["has_faq_section"] or faq_signals["has_faq_schema"]) else 0,
        10 if has_sitemap else 0,
        5 if has_robots else 0,
        10 if h1s else 0,
        10 if phone_numbers else 0,
        10 if (total_images == 0 or images_missing_alt / max(total_images, 1) < 0.3) else 0,
        10 if og_title else 0,
    ])

    wins, issues, recommendations = [], [], []
    if is_https:
        wins.append("Site served over HTTPS")
    else:
        issues.append("Not using HTTPS — a trust and ranking signal")
    if schema_found:
        wins.append(f"Schema markup detected: {', '.join(set(schema_found[:3]))}")
    else:
        issues.append("No schema markup — invisible to rich results and AI search")
        recommendations.append("Add LocalBusiness schema markup to your homepage")
    if meta_desc:
        wins.append("Meta description is set")
    else:
        issues.append("Missing meta description")
        recommendations.append("Write a compelling meta description under 160 characters")
    if faq_signals["has_faq_section"] or faq_signals["has_faq_schema"]:
        wins.append("FAQ section detected — strong AEO signal")
    else:
        issues.append("No FAQ section — missing AI answer engine opportunity")
        recommendations.append("Add an FAQ section to address common customer questions")
    if has_sitemap:
        wins.append("sitemap.xml found")
    else:
        issues.append("No sitemap.xml detected")
    if phone_numbers:
        wins.append(f"Phone number present: {phone_numbers[0]}")
    else:
        issues.append("No phone number found on site")
    if images_missing_alt > 0:
        issues.append(f"{images_missing_alt} of {total_images} images missing alt text")

    return {
        "channel": "website",
        "score": min(score, 100),
        "wins": wins,
        "issues": issues,
        "recommendations": recommendations[:3],
        "url": url,
        "title": title,
        "meta_description": meta_desc,
        "meta_keywords": meta_keywords,
        "og_title": og_title,
        "og_description": og_desc,
        "h1s": h1s,
        "h2s": h2s,
        "h3s": h3s,
        "body_text_sample": body_text[:4000],
        "total_images": total_images,
        "images_missing_alt": images_missing_alt,
        "internal_link_count": len(internal_links),
        "external_link_count": len(external_links),
        "schema_types_found": schema_found,
        "faq_signals": faq_signals,
        "phone_numbers_found": phone_numbers,
        "has_address_signals": address_signals,
        "has_sitemap": has_sitemap,
        "has_robots_txt": has_robots,
        "is_https": is_https,
        "pages_crawled": list(pages_html.keys()),
        "brand_colors": brand_colors,
        "logo_url": logo_url,
        "brand_identity": brand_identity,
        "social_urls_found": social_urls_found,
    }

# ─────────────────────────────────────────
# CHANNEL 2: GBP
# ─────────────────────────────────────────
async def audit_gbp(business_name: str, location: str, phone: str) -> dict:
    _default = {
        "channel": "gbp",
        "score": 0,
        "wins": [],
        "issues": ["GBP data unavailable — GOOGLE_API_KEY not configured or business_name missing"],
        "recommendations": ["Claim and optimize your Google Business Profile at business.google.com"],
        "rating": None,
        "review_count": 0,
        "is_verified": False,
        "has_hours": False,
        "has_photos": False,
        "photo_count": 0,
        "website_matches": False,
        "completeness_gaps": [],
        "place_id": None,
        "formatted_address": "",
        "business_status": "",
    }

    if not GOOGLE_API_KEY or not business_name:
        return _default

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            search_resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params={"query": f"{business_name} {location}".strip(), "key": GOOGLE_API_KEY}
            )
            if search_resp.status_code != 200:
                return {**_default, "issues": ["GBP search API returned an error"]}

            results = search_resp.json().get("results", [])
            if not results:
                return {**_default, "issues": [f"No GBP listing found for '{business_name}' in '{location}'"]}

            place_id = results[0].get("place_id", "")
            detail_resp = await client.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields": "name,rating,user_ratings_total,formatted_address,formatted_phone_number,website,opening_hours,photos,business_status,price_level,types",
                    "key": GOOGLE_API_KEY
                }
            )
            if detail_resp.status_code != 200:
                return {**_default, "issues": ["GBP details API returned an error"]}

            d = detail_resp.json().get("result", {})
    except Exception as e:
        return {**_default, "issues": [f"GBP lookup failed: {str(e)}"]}

    rating = d.get("rating")
    review_count = d.get("user_ratings_total", 0)
    formatted_address = d.get("formatted_address", "")
    has_hours = bool(d.get("opening_hours", {}).get("weekday_text"))
    photos = d.get("photos", [])
    photo_count = len(photos)
    has_photos = photo_count >= 1
    website_matches = bool(d.get("website"))
    business_status = d.get("business_status", "")

    completeness_gaps = []
    if not has_hours:
        completeness_gaps.append("Missing business hours")
    if photo_count < 5:
        completeness_gaps.append(f"Low photo count ({photo_count} — aim for 5+)")
    if not website_matches:
        completeness_gaps.append("No website linked on GBP")
    if review_count < 10:
        completeness_gaps.append(f"Low review count ({review_count} — aim for 10+)")
    if rating and rating < 4.0:
        completeness_gaps.append(f"Rating below 4.0 ({rating} stars)")
    if not d.get("formatted_phone_number"):
        completeness_gaps.append("No phone number on GBP listing")

    score = 0
    if rating:
        score += min(30, round((rating / 5.0) * 30))
    score += min(25, round((min(review_count, 200) / 200) * 25))
    score += max(0, 25 - len(completeness_gaps) * 5)
    if has_photos:
        score += min(20, round((min(photo_count, 10) / 10) * 20))

    wins, issues, recommendations = [], [], []
    if rating and rating >= 4.0:
        wins.append(f"Strong rating: {rating} stars")
    elif rating:
        issues.append(f"Rating is {rating} stars — below the 4.0 threshold that drives clicks")
        recommendations.append("Respond to all reviews and encourage satisfied customers to leave 5-star reviews")
    if review_count >= 50:
        wins.append(f"Strong review volume: {review_count} reviews")
    elif review_count >= 10:
        wins.append(f"{review_count} reviews — growing social proof")
    else:
        issues.append(f"Only {review_count} reviews — needs review generation strategy")
        recommendations.append("Launch a review request campaign targeting past customers via text or email")
    if has_hours:
        wins.append("Business hours are set")
    else:
        issues.append("No business hours on GBP — customers can't tell when you're open")
        recommendations.append("Add accurate business hours to your GBP listing immediately")
    if has_photos and photo_count >= 5:
        wins.append(f"{photo_count} photos on listing")
    else:
        issues.append(f"Only {photo_count} photos — listings with 10+ photos get significantly more views")
        recommendations.append("Upload at least 10 high-quality photos showing your work, team, and location")
    if website_matches:
        wins.append("Website is linked on GBP")
    else:
        issues.append("No website linked on GBP listing")
        recommendations.append("Add your website URL to your Google Business Profile")

    return {
        "channel": "gbp",
        "score": min(score, 100),
        "wins": wins,
        "issues": issues,
        "recommendations": recommendations[:3],
        "rating": rating,
        "review_count": review_count,
        "is_verified": business_status == "OPERATIONAL",
        "has_hours": has_hours,
        "has_photos": has_photos,
        "photo_count": photo_count,
        "website_matches": website_matches,
        "completeness_gaps": completeness_gaps,
        "place_id": place_id,
        "formatted_address": formatted_address,
        "business_status": business_status,
    }

# ─────────────────────────────────────────
# CHANNEL 3: LSA (SERP Scrape via Playwright)
# ─────────────────────────────────────────
async def audit_lsa(business_name: str, location: str, service_category: str = "") -> dict:
    _default = {
        "channel": "lsa",
        "score": 20,
        "wins": [],
        "issues": ["LSA/SERP presence could not be determined"],
        "recommendations": ["Apply for Google Local Services Ads at ads.google.com/local-services-ads"],
        "is_lsa_present": False,
        "is_google_guaranteed": False,
        "local_pack_present": False,
        "local_pack_position": None,
        "searches_performed": [],
    }

    if not business_name:
        return _default

    is_lsa_present = False
    is_google_guaranteed = False
    local_pack_present = False
    local_pack_position = None
    searches_performed = []

    queries = [f"{business_name} {location}".strip()]
    if service_category:
        city = location.split(",")[0].strip() if location else ""
        queries.append(f"{business_name} {city} {service_category}".strip())

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US"
            )
            page = await context.new_page()

            for query in queries[:2]:
                try:
                    searches_performed.append(query)
                    await page.goto(
                        f"https://www.google.com/search?q={query.replace(' ', '+')}",
                        wait_until="domcontentloaded",
                        timeout=15000
                    )
                    await page.wait_for_timeout(1500)

                    html = await page.content()
                    soup_g = BeautifulSoup(html, "html.parser")
                    page_text = soup_g.get_text().lower()

                    if "google guaranteed" in page_text or "google screened" in page_text:
                        is_google_guaranteed = True
                        is_lsa_present = True

                    # Local pack detection — map results container signals
                    local_pack_signals = [
                        soup_g.find("div", attrs={"data-local-attribute": True}),
                        soup_g.find(class_=re.compile(r"VkpGBb|rllt__details|uMdZh|lu_map", re.I)),
                    ]
                    if any(local_pack_signals):
                        local_pack_present = True

                    # Fallback: look for "open now", "directions", and star ratings together
                    if not local_pack_present and (
                        "directions" in page_text and
                        ("open now" in page_text or "stars" in page_text) and
                        business_name.lower()[:6] in page_text
                    ):
                        local_pack_present = True

                    if local_pack_present and business_name.lower()[:6] in page_text:
                        local_pack_position = 1
                except Exception:
                    continue

            await browser.close()
    except Exception as e:
        return {**_default, "issues": [f"LSA/SERP check failed: {str(e)}"]}

    score = 100 if is_google_guaranteed else (80 if is_lsa_present else (60 if local_pack_present else 20))

    wins, issues, recommendations = [], [], []
    if is_google_guaranteed:
        wins.append("Google Guaranteed badge detected — the highest trust signal in local search")
    elif is_lsa_present:
        wins.append("Local Services Ad presence detected")
    else:
        issues.append("No Google Guaranteed or LSA presence detected")
        recommendations.append("Apply for Google Local Services Ads to gain the Google Guaranteed badge")

    if local_pack_present:
        wins.append("Business appears in Google local pack (map results)")
        if local_pack_position:
            wins.append(f"Local pack position: #{local_pack_position}")
    else:
        issues.append("Not appearing in Google local pack for key brand searches")
        recommendations.append("Optimize GBP completeness and build local citations to improve local pack ranking")

    if not is_google_guaranteed and not is_lsa_present:
        issues.append("Missing premium ad placement above organic results")
        recommendations.append("LSA typically delivers leads at $15-50 CPL vs $100+ from traditional PPC")

    return {
        "channel": "lsa",
        "score": score,
        "wins": wins,
        "issues": issues,
        "recommendations": recommendations[:3],
        "is_lsa_present": is_lsa_present,
        "is_google_guaranteed": is_google_guaranteed,
        "local_pack_present": local_pack_present,
        "local_pack_position": local_pack_position,
        "searches_performed": searches_performed,
    }

# ─────────────────────────────────────────
# CHANNEL 4: YOUTUBE
# ─────────────────────────────────────────
async def audit_youtube(business_name: str, youtube_url: str) -> dict:
    _default = {
        "channel": "youtube",
        "score": 0,
        "wins": [],
        "issues": ["No YouTube channel found"],
        "recommendations": ["Create a YouTube channel and publish educational content about your services"],
        "has_channel": False,
        "channel_name": "",
        "channel_id": "",
        "subscriber_count": 0,
        "video_count": 0,
        "view_count": 0,
        "last_upload_date": None,
        "upload_cadence": 0,
        "has_recent_content": False,
    }

    if not GOOGLE_API_KEY:
        return {**_default, "issues": ["YouTube audit skipped — GOOGLE_API_KEY not configured"]}

    channel_id = ""

    if youtube_url:
        yt_match = re.search(r'youtube\.com/(?:channel/|@|c/|user/)([^/?&\s]+)', youtube_url)
        if yt_match:
            handle = yt_match.group(1)
            if handle.startswith("UC"):
                channel_id = handle
            else:
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.get(
                            "https://www.googleapis.com/youtube/v3/search",
                            params={"part": "snippet", "q": handle, "type": "channel", "maxResults": 1, "key": GOOGLE_API_KEY}
                        )
                        if resp.status_code == 200:
                            items = resp.json().get("items", [])
                            if items:
                                channel_id = items[0]["snippet"]["channelId"]
                except Exception:
                    pass

    if not channel_id and business_name:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={"part": "snippet", "q": business_name, "type": "channel", "maxResults": 1, "key": GOOGLE_API_KEY}
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        channel_id = items[0]["snippet"]["channelId"]
        except Exception as e:
            return {**_default, "issues": [f"YouTube search failed: {str(e)}"]}

    if not channel_id:
        return _default

    channel_name = ""
    subscriber_count = 0
    video_count = 0
    view_count = 0
    last_upload_date = None
    upload_cadence = 0
    has_recent_content = False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            stats_resp = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics,snippet", "id": channel_id, "key": GOOGLE_API_KEY}
            )
            if stats_resp.status_code != 200:
                return {**_default, "issues": ["YouTube channel stats unavailable"]}

            channel_items = stats_resp.json().get("items", [])
            if not channel_items:
                return _default

            ch = channel_items[0]
            stats = ch.get("statistics", {})
            channel_name = ch.get("snippet", {}).get("title", "")
            subscriber_count = int(stats.get("subscriberCount", 0) or 0)
            video_count = int(stats.get("videoCount", 0) or 0)
            view_count = int(stats.get("viewCount", 0) or 0)

            videos_resp = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "channelId": channel_id, "order": "date", "maxResults": 10, "key": GOOGLE_API_KEY}
            )
            if videos_resp.status_code == 200:
                video_items = videos_resp.json().get("items", [])
                dates = []
                for v in video_items:
                    pub = v.get("snippet", {}).get("publishedAt", "")
                    if pub:
                        try:
                            dates.append(datetime.fromisoformat(pub.replace("Z", "+00:00")))
                        except Exception:
                            pass
                if dates:
                    dates.sort(reverse=True)
                    last_upload_date = dates[0].strftime("%Y-%m-%d")
                    days_since = (datetime.now(timezone.utc) - dates[0]).days
                    has_recent_content = days_since <= 90
                    if len(dates) >= 2:
                        span_days = max((dates[0] - dates[-1]).days, 1)
                        upload_cadence = round((len(dates) / span_days) * 30, 1)
    except Exception as e:
        return {**_default, "issues": [f"YouTube data fetch failed: {str(e)}"]}

    score = 0
    if channel_id:
        score = 10
        if video_count > 0:
            score = 20
        if has_recent_content:
            score = 50
        if has_recent_content and subscriber_count >= 1000:
            score = 75
        if has_recent_content and subscriber_count >= 10000:
            score = 90

    wins, issues, recommendations = [], [], []
    wins.append(f"YouTube channel found: {channel_name}")
    if subscriber_count >= 1000:
        wins.append(f"{subscriber_count:,} subscribers")
    elif subscriber_count > 0:
        issues.append(f"Only {subscriber_count:,} subscribers — channel needs growth strategy")
        recommendations.append("Use SEO-optimized titles and thumbnails to grow subscribers")
    if has_recent_content:
        wins.append(f"Active channel — last upload: {last_upload_date}")
    elif last_upload_date:
        issues.append(f"Channel is stale — last upload was {last_upload_date}")
        recommendations.append("Resume publishing at minimum 2x per month to stay relevant in YouTube search")
    else:
        issues.append("No videos found on channel")
        recommendations.append("Start publishing educational videos about your services")
    if upload_cadence >= 4:
        wins.append(f"Strong upload cadence: ~{upload_cadence} videos/month")
    elif 0 < upload_cadence < 4:
        issues.append(f"Low upload frequency: ~{upload_cadence} videos/month")
    if video_count >= 50:
        wins.append(f"Strong video library: {video_count} total videos")

    return {
        "channel": "youtube",
        "score": score,
        "wins": wins,
        "issues": issues,
        "recommendations": recommendations[:3],
        "has_channel": True,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "subscriber_count": subscriber_count,
        "video_count": video_count,
        "view_count": view_count,
        "last_upload_date": last_upload_date,
        "upload_cadence": upload_cadence,
        "has_recent_content": has_recent_content,
    }

# ─────────────────────────────────────────
# CHANNEL 5: SOCIALS (Playwright scrape)
# ─────────────────────────────────────────
async def audit_socials(
    website_data: dict,
    facebook_url: str,
    instagram_url: str,
    linkedin_url: str
) -> dict:
    _default = {
        "channel": "socials",
        "score": 0,
        "wins": [],
        "issues": ["No social media profiles found or provided"],
        "recommendations": [
            "Create a Facebook business page — essential for local business visibility",
            "Create a LinkedIn company page to build B2B credibility",
        ],
        "platforms_found": [],
        "total_reach": 0,
        "last_active_platform": None,
        "days_since_last_post": None,
        "is_active": False,
        "platform_details": {},
    }

    discovered = website_data.get("social_urls_found", {})
    to_check = {}
    if facebook_url or discovered.get("facebook"):
        to_check["facebook"] = facebook_url or discovered["facebook"]
    if instagram_url or discovered.get("instagram"):
        to_check["instagram"] = instagram_url or discovered["instagram"]
    if linkedin_url or discovered.get("linkedin"):
        to_check["linkedin"] = linkedin_url or discovered["linkedin"]

    if not to_check:
        return _default

    platform_details = {}
    total_reach = 0
    platforms_found = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            for platform, purl in to_check.items():
                page = await context.new_page()
                try:
                    await page.goto(purl, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(1500)
                    html = await page.content()
                    soup_s = BeautifulSoup(html, "html.parser")
                    page_text = soup_s.get_text()

                    details = {"url": purl, "accessible": True}
                    followers = 0

                    if platform == "facebook":
                        m = re.search(r'([\d,]+)\s*(?:people follow|followers|likes)', page_text, re.IGNORECASE)
                        if m:
                            followers = int(m.group(1).replace(",", ""))
                        details["followers"] = followers
                        details["is_verified"] = bool(soup_s.find(attrs={"aria-label": re.compile("verified", re.I)}))

                    elif platform == "instagram":
                        m = re.search(r'([\d,.]+[KMk]?)\s*[Ff]ollowers', page_text)
                        if m:
                            raw = m.group(1).replace(",", "").upper()
                            try:
                                if "K" in raw:
                                    followers = int(float(raw.replace("K", "")) * 1000)
                                elif "M" in raw:
                                    followers = int(float(raw.replace("M", "")) * 1_000_000)
                                else:
                                    followers = int(raw)
                            except Exception:
                                pass
                        details["followers"] = followers
                        pm = re.search(r'([\d,]+)\s*posts', page_text, re.IGNORECASE)
                        details["post_count"] = int(pm.group(1).replace(",", "")) if pm else 0

                    elif platform == "linkedin":
                        m = re.search(r'([\d,]+)\s*followers', page_text, re.IGNORECASE)
                        if m:
                            followers = int(m.group(1).replace(",", ""))
                        details["followers"] = followers
                        em = re.search(r'([\d,\-]+)\s*employees', page_text, re.IGNORECASE)
                        details["employee_range"] = em.group(1) if em else ""

                    total_reach += followers
                    platform_details[platform] = details
                    platforms_found.append(platform)
                except Exception as e:
                    platform_details[platform] = {"url": purl, "accessible": False, "error": str(e)}
                finally:
                    await page.close()

            await browser.close()
    except Exception as e:
        return {**_default, "issues": [f"Social scrape failed: {str(e)}"]}

    if not platforms_found:
        return _default

    num = len(platforms_found)
    score = 0
    if num == 1:
        score = 20
    elif num >= 2:
        score = 40
        if total_reach >= 1000:
            score = 75
        if total_reach >= 10000:
            score = 90

    wins, issues, recommendations = [], [], []
    wins.append(f"Active on {num} social platform(s): {', '.join(platforms_found)}")
    if total_reach >= 1000:
        wins.append(f"Total social reach: {total_reach:,} followers across platforms")
    elif total_reach > 0:
        issues.append(f"Low social reach: {total_reach:,} total followers")
        recommendations.append("Focus on consistent content and engagement to grow follower counts")
    for platform, details in platform_details.items():
        if not details.get("accessible"):
            issues.append(f"{platform.title()} profile restricted — could not fully analyze")
    if "facebook" not in platforms_found:
        issues.append("No Facebook business page detected")
        recommendations.append("Create a Facebook business page — essential for local business visibility")
    if "linkedin" not in platforms_found:
        issues.append("No LinkedIn company page found")
        recommendations.append("Create a LinkedIn company page to build B2B credibility")

    return {
        "channel": "socials",
        "score": min(score, 100),
        "wins": wins,
        "issues": issues,
        "recommendations": recommendations[:3],
        "platforms_found": platforms_found,
        "total_reach": total_reach,
        "last_active_platform": platforms_found[-1] if platforms_found else None,
        "days_since_last_post": None,
        "is_active": len(platforms_found) > 0,
        "platform_details": platform_details,
    }

# ─────────────────────────────────────────
# SHARED AUDIT RUNNER
# ─────────────────────────────────────────
async def _run_full_audit(request: AuditRequest, clean_url: str) -> tuple:
    try:
        website_data = await scrape_website(clean_url)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not reach website: {str(e)}")

    def _safe(r, fallback):
        return r if not isinstance(r, Exception) else fallback

    results = await asyncio.gather(
        get_pagespeed(clean_url),
        audit_gbp(request.business_name, request.location, request.phone),
        audit_lsa(request.business_name, request.location),
        audit_youtube(request.business_name, request.youtube_url),
        audit_socials(website_data, request.facebook_url, request.instagram_url, request.linkedin_url),
        return_exceptions=True
    )

    pagespeed = _safe(results[0], {"mobile_score": None, "desktop_score": None, "core_web_vitals": {}})
    gbp      = _safe(results[1], {"channel": "gbp",     "score": 0, "wins": [], "issues": ["GBP data unavailable"],     "recommendations": []})
    lsa      = _safe(results[2], {"channel": "lsa",     "score": 0, "wins": [], "issues": ["LSA data unavailable"],     "recommendations": []})
    youtube  = _safe(results[3], {"channel": "youtube", "score": 0, "wins": [], "issues": ["YouTube data unavailable"], "recommendations": []})
    socials  = _safe(results[4], {"channel": "socials", "score": 0, "wins": [], "issues": ["Socials data unavailable"], "recommendations": []})

    website_data["pagespeed"] = pagespeed
    if pagespeed.get("mobile_score") is not None:
        mobile = pagespeed["mobile_score"]
        website_data["score"] = min(100, website_data.get("score", 0) + round(mobile * 0.15))
        if mobile >= 80:
            website_data.setdefault("wins", []).append(f"Mobile PageSpeed score: {mobile}/100")
        elif mobile < 50:
            website_data.setdefault("issues", []).append(f"Poor mobile PageSpeed score: {mobile}/100")

    channel_data = {"website": website_data, "gbp": gbp, "lsa": lsa, "youtube": youtube, "socials": socials}

    report = await run_audit(
        channel_data=channel_data,
        business_name=request.business_name,
        contact_name=request.contact_name,
        challenge=request.challenge,
        location=request.location,
    )
    return website_data, report

# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "IA Immersive Authority & Visibility Audit Engine is live", "version": "3.0.0"}

@app.post("/audit")
async def audit_endpoint(request: AuditRequest):
    clean_url = normalize_url(request.url)
    website_data, report = await _run_full_audit(request, clean_url)

    auth_score = report.get("authority_score", 0)
    vis_score  = report.get("visibility_score", 0)

    return {
        "status": "success",
        "url_submitted": request.url,
        "url_analyzed": clean_url,
        "business_name": request.business_name,
        "contact_name": request.contact_name,
        "brand_colors": website_data.get("brand_colors", {}),
        "logo_url": website_data.get("logo_url", ""),
        "authority_score": auth_score,
        "visibility_score": vis_score,
        "authority_grade": report.get("authority_grade", "F"),
        "visibility_grade": report.get("visibility_grade", "F"),
        "authority_score_color": score_color(auth_score),
        "authority_score_label": score_label(auth_score),
        "visibility_score_color": score_color(vis_score),
        "visibility_score_label": score_label(vis_score),
        "report": report,
    }

@app.post("/audit-with-pdf")
async def audit_with_pdf_endpoint(request: AuditRequest):
    from pdf_generator import generate_pdf_base64
    clean_url = normalize_url(request.url)
    website_data, report = await _run_full_audit(request, clean_url)

    auth_score = report.get("authority_score", 0)
    vis_score  = report.get("visibility_score", 0)

    audit_data = {
        "status": "success",
        "url_submitted": request.url,
        "url_analyzed": clean_url,
        "business_name": request.business_name,
        "contact_name": request.contact_name,
        "brand_colors": website_data.get("brand_colors", {}),
        "logo_url": website_data.get("logo_url", ""),
        "authority_score": auth_score,
        "visibility_score": vis_score,
        "authority_grade": report.get("authority_grade", "F"),
        "visibility_grade": report.get("visibility_grade", "F"),
        "authority_score_color": score_color(auth_score),
        "authority_score_label": score_label(auth_score),
        "visibility_score_color": score_color(vis_score),
        "visibility_score_label": score_label(vis_score),
        "report": report,
    }
    pdf_base64 = generate_pdf_base64(audit_data)
    return {
        **audit_data,
        "pdf_base64": pdf_base64,
        "pdf_filename": f"IA-Authority-Audit-{request.business_name.replace(' ', '-')}.pdf",
    }
