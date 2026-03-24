import os
import re
import json
import httpx
from collections import Counter
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse
from bs4 import BeautifulSoup
from audit import run_audit

app = FastAPI(title="IA Website Intelligence Engine", version="2.0.0")

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
        # Filter near-white, near-black, and pure grays
        if max(r,g,b) < 30 or min(r,g,b) > 225:
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
    from urllib.parse import urlparse

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

    # 1. Logo by class/id/alt — most reliable
    for attr in [
        {"class": re.compile(r"logo", re.I)},
        {"id": re.compile(r"logo", re.I)},
        {"alt": re.compile(r"logo", re.I)},
        {"class": re.compile(r"site-logo|brand-logo|navbar-brand", re.I)},
    ]:
        candidates = soup.find_all("img", attrs=attr)
        for c in candidates:
            src = c.get("src", "") or c.get("data-src", "")
            resolved = resolve(src)
            if resolved:
                return resolved

    # 2. Header/nav img
    for header_tag in ["header", "nav"]:
        section = soup.find(header_tag)
        if section:
            img = section.find("img")
            if img:
                src = img.get("src", "") or img.get("data-src", "")
                resolved = resolve(src)
                if resolved:
                    return resolved

    # 3. OG image last — often a social share graphic, not the logo
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        return og_image["content"]

    return ""

# ─────────────────────────────────────────
# BRAND IDENTITY EXTRACTOR
# ─────────────────────────────────────────
def extract_brand_identity(soup: BeautifulSoup, body_text: str) -> dict:
    # Tagline from H1
    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    tagline = h1s[0][:200] if h1s else ""

    # Mission
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

    # Leadership — check schema first, then HTML elements, then text patterns
    leadership = []
    titles = r'(?:CEO|CTO|COO|CFO|CMO|CIO|Founder|Co-Founder|President|Owner|Director|Principal|Partner|Managing)'

    # 1. JSON-LD schema — most reliable
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json as _json
            data = _json.loads(script.string or "")
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

    # 2. HTML elements with title/role nearby (team cards, about pages)
    if not leadership:
        for el in soup.find_all(["h2", "h3", "h4", "p", "span", "div"]):
            text = el.get_text(strip=True)
            # Look for "Name, Title" or "Name - Title" patterns
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

    # 3. Plain text regex fallback
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

    # Founded year
    founded = ""
    year_match = re.search(r'(?:founded|established|since|started)[^0-9]*(\d{4})', body_text, re.IGNORECASE)
    if year_match:
        year = int(year_match.group(1))
        if 1900 < year < 2030:
            founded = str(year)

    # Location
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
# SCRAPER
# ─────────────────────────────────────────
async def scrape_website(url: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; IAIntelligenceBot/1.0)"}

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not reach website: {str(e)}")

    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title else ""
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag else ""
    meta_keywords_tag = soup.find("meta", attrs={"name": "keywords"})
    meta_keywords = meta_keywords_tag["content"].strip() if meta_keywords_tag else ""

    og_title = ""
    og_desc = ""
    og_tag = soup.find("meta", property="og:title")
    if og_tag:
        og_title = og_tag.get("content", "")
    og_dtag = soup.find("meta", property="og:description")
    if og_dtag:
        og_desc = og_dtag.get("content", "")

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")][:10]
    h3s = [h.get_text(strip=True) for h in soup.find_all("h3")][:10]

    # Extract brand data BEFORE decomposing style tags
    brand_colors = extract_brand_colors(html, soup)
    logo_url = extract_logo(soup, url)

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body_text = soup.get_text(separator=" ", strip=True)
    body_text = re.sub(r'\s+', ' ', body_text)[:6000]

    brand_identity = extract_brand_identity(soup, body_text)

    images = soup.find_all("img")
    total_images = len(images)
    images_missing_alt = len([i for i in images if not i.get("alt", "").strip()])

    all_links = soup.find_all("a", href=True)
    internal_links = [a["href"] for a in all_links if url.split("/")[2] in a["href"] or a["href"].startswith("/")]
    external_links = [a["href"] for a in all_links if a["href"].startswith("http") and url.split("/")[2] not in a["href"]]

    schema_tags = soup.find_all("script", type="application/ld+json")
    schema_found = []
    for tag in schema_tags:
        try:
            data = json.loads(tag.string)
            schema_type = data.get("@type", "Unknown") if isinstance(data, dict) else "Multiple"
            schema_found.append(schema_type)
        except:
            pass

    text_lower = body_text.lower()
    faq_signals = {
        "has_faq_section": bool(soup.find(id=re.compile("faq", re.I)) or soup.find(class_=re.compile("faq", re.I))),
        "question_headers": len([h for h in h2s + h3s if any(q in h.lower() for q in ["how", "what", "why", "when", "where", "who", "can", "do ", "is "])]),
        "has_faq_schema": any("faqpage" in s.lower() for s in schema_found),
    }

    phone_pattern = re.findall(r'(\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4})', body_text)
    unique_phones = list(set(phone_pattern))
    address_signals = any(word in text_lower for word in ["street", "ave", "blvd", "suite", "ste.", " tx ", " texas "])

    has_sitemap = False
    has_robots = False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            sm = await client.get(f"{url}/sitemap.xml")
            has_sitemap = sm.status_code == 200
            rb = await client.get(f"{url}/robots.txt")
            has_robots = rb.status_code == 200
    except:
        pass

    is_https = url.startswith("https")

    return {
        "url": url,
        "title": title,
        "meta_description": meta_desc,
        "meta_keywords": meta_keywords,
        "og_title": og_title,
        "og_description": og_desc,
        "h1s": h1s,
        "h2s": h2s,
        "h3s": h3s,
        "body_text_sample": body_text,
        "total_images": total_images,
        "images_missing_alt": images_missing_alt,
        "internal_link_count": len(internal_links),
        "external_link_count": len(external_links),
        "schema_types_found": schema_found,
        "faq_signals": faq_signals,
        "phone_numbers_found": unique_phones,
        "has_address_signals": address_signals,
        "has_sitemap": has_sitemap,
        "has_robots_txt": has_robots,
        "is_https": is_https,
        "brand_colors": brand_colors,
        "logo_url": logo_url,
        "brand_identity": brand_identity,
    }

# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "IA Intelligence Engine is live", "version": "2.0.0"}

@app.post("/audit")
async def audit_endpoint(request: AuditRequest):
    clean_url = normalize_url(request.url)
    scraped = await scrape_website(clean_url)
    report = await run_audit(
        scraped_data=scraped,
        business_name=request.business_name,
        contact_name=request.contact_name,
        challenge=request.challenge
    )
    def score_color(s): return "#22c55e" if s > 65 else ("#f59e0b" if s > 40 else "#ef4444")
    def score_label(s): return "Strong" if s > 65 else ("Moderate" if s > 40 else "Critical")
    report["seo_score_color"] = score_color(report.get("seo_score", 0))
    report["aeo_score_color"] = score_color(report.get("aeo_score", 0))
    report["geo_score_color"] = score_color(report.get("geo_score", 0))
    report["seo_score_label"] = score_label(report.get("seo_score", 0))
    report["aeo_score_label"] = score_label(report.get("aeo_score", 0))
    report["geo_score_label"] = score_label(report.get("geo_score", 0))
    return {
        "status": "success",
        "url_submitted": request.url,
        "url_analyzed": clean_url,
        "business_name": request.business_name,
        "contact_name": request.contact_name,
        "brand_colors": scraped["brand_colors"],
        "logo_url": scraped["logo_url"],
        "brand_identity": scraped["brand_identity"],
        "brand_intelligence": report.get("brand_intelligence", {}),
        "report": report
    }

@app.post("/audit-with-pdf")
async def audit_with_pdf_endpoint(request: AuditRequest):
    from pdf_generator import generate_pdf_base64
    clean_url = normalize_url(request.url)
    scraped = await scrape_website(clean_url)
    report = await run_audit(
        scraped_data=scraped,
        business_name=request.business_name,
        contact_name=request.contact_name,
        challenge=request.challenge
    )
    audit_data = {
        "status": "success",
        "url_submitted": request.url,
        "url_analyzed": clean_url,
        "business_name": request.business_name,
        "contact_name": request.contact_name,
        "brand_colors": scraped["brand_colors"],
        "logo_url": scraped["logo_url"],
        "brand_identity": scraped["brand_identity"],
        "report": report
    }
    pdf_base64 = generate_pdf_base64(audit_data)
    return {
        **audit_data,
        "pdf_base64": pdf_base64,
        "pdf_filename": f"IA-Audit-{request.business_name.replace(' ', '-')}.pdf"
    }
