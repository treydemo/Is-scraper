import os
import re
import json
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse
from bs4 import BeautifulSoup
from audit import run_audit
from pdf_generator import generate_pdf_base64

app = FastAPI(title="IA Website Intelligence Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# REQUEST MODEL
# ─────────────────────────────────────────
class AuditRequest(BaseModel):
    url: str
    business_name: str = ""
    contact_name: str = ""
    challenge: str = ""

# ─────────────────────────────────────────
# URL NORMALIZER — feral-proof
# ─────────────────────────────────────────
def normalize_url(raw: str) -> str:
    url = raw.strip().lower()
    url = re.sub(r'\s+', '', url)          # kill all whitespace
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    # Force https
    normalized = urlunparse(parsed._replace(scheme="https"))
    # Strip trailing slash
    return normalized.rstrip("/")

# ─────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────
async def scrape_website(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; IAIntelligenceBot/1.0)"
    }

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html = response.text
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not reach website: {str(e)}")

    soup = BeautifulSoup(html, "html.parser")

    # ── META & TITLE ──
    title = soup.title.string.strip() if soup.title else ""
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag else ""
    meta_keywords_tag = soup.find("meta", attrs={"name": "keywords"})
    meta_keywords = meta_keywords_tag["content"].strip() if meta_keywords_tag else ""

    # ── OG TAGS ──
    og_title = ""
    og_desc = ""
    og_tag = soup.find("meta", property="og:title")
    if og_tag:
        og_title = og_tag.get("content", "")
    og_dtag = soup.find("meta", property="og:description")
    if og_dtag:
        og_desc = og_dtag.get("content", "")

    # ── HEADINGS ──
    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h2s = [h.get_text(strip=True) for h in soup.find_all("h2")][:10]
    h3s = [h.get_text(strip=True) for h in soup.find_all("h3")][:10]

    # ── BODY TEXT ──
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body_text = soup.get_text(separator=" ", strip=True)
    body_text = re.sub(r'\s+', ' ', body_text)[:6000]  # cap at 6k chars

    # ── IMAGES ──
    images = soup.find_all("img")
    total_images = len(images)
    images_missing_alt = len([i for i in images if not i.get("alt", "").strip()])

    # ── LINKS ──
    all_links = soup.find_all("a", href=True)
    internal_links = [a["href"] for a in all_links if url.split("/")[2] in a["href"] or a["href"].startswith("/")]
    external_links = [a["href"] for a in all_links if a["href"].startswith("http") and url.split("/")[2] not in a["href"]]

    # ── SCHEMA MARKUP ──
    schema_tags = soup.find_all("script", type="application/ld+json")
    schema_found = []
    for tag in schema_tags:
        try:
            data = json.loads(tag.string)
            schema_type = data.get("@type", "Unknown") if isinstance(data, dict) else "Multiple"
            schema_found.append(schema_type)
        except:
            pass

    # ── FAQ SIGNALS ──
    text_lower = body_text.lower()
    faq_signals = {
        "has_faq_section": bool(soup.find(id=re.compile("faq", re.I)) or soup.find(class_=re.compile("faq", re.I))),
        "question_headers": len([h for h in h2s + h3s if any(q in h.lower() for q in ["how", "what", "why", "when", "where", "who", "can", "do ", "is "])]),
        "has_faq_schema": any("faqpage" in s.lower() for s in schema_found),
    }

    # ── NAP (Name/Address/Phone) ──
    phone_pattern = re.findall(r'(\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4})', body_text)
    unique_phones = list(set(phone_pattern))

    address_signals = any(word in text_lower for word in ["street", "ave", "blvd", "suite", "ste.", " tx ", " texas "])

    # ── PERFORMANCE SIGNALS ──
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

    # ── SSL ──
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
    }

# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "IA Intelligence Engine is live", "version": "1.0.0"}

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
    return {
        "status": "success",
        "url_submitted": request.url,
        "url_analyzed": clean_url,
        "business_name": request.business_name,
        "contact_name": request.contact_name,
        "report": report
    }

@app.post("/audit-with-pdf")
async def audit_with_pdf_endpoint(request: AuditRequest):
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
        "report": report
    }
    pdf_base64 = generate_pdf_base64(audit_data)
    return {
        **audit_data,
        "pdf_base64": pdf_base64,
        "pdf_filename": f"IA-Audit-{request.business_name.replace(' ', '-')}.pdf"
    }
