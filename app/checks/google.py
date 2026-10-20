"""Google data via official APIs (no SERP scraping): Places API (New), PageSpeed Insights, YouTube Data API."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from .. import config
from ..fetch import domain_of

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_FIELDS = ",".join(f"places.{f}" for f in [
    "id", "displayName", "formattedAddress", "nationalPhoneNumber", "websiteUri", "rating",
    "userRatingCount", "regularOpeningHours", "photos", "businessStatus", "primaryType",
    "primaryTypeDisplayName", "reviews", "googleMapsUri", "location", "addressComponents",
])
STOP = {"the", "a", "an", "of", "and", "llc", "inc", "co", "company", "corp", "pllc", "pc", "ltd", "&"}


def digits(s: str | None) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d


def name_tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP and len(w) > 1}


async def places_search(client: httpx.AsyncClient, query: str, center: dict | None = None,
                        radius_m: float = 30000, max_results: int = 10) -> tuple[list[dict], str | None]:
    if not config.GOOGLE_API_KEY:
        return [], "not configured"
    body = {"textQuery": query, "maxResultCount": max_results, "languageCode": "en"}
    if center:
        body["locationBias"] = {"circle": {"center": center, "radius": radius_m}}
    try:
        r = await client.post(PLACES_URL, json=body, timeout=20, headers={
            "X-Goog-Api-Key": config.GOOGLE_API_KEY, "X-Goog-FieldMask": PLACE_FIELDS})
        if r.status_code != 200:
            return [], f"Places HTTP {r.status_code}: {r.text[:200]}"
        return r.json().get("places", []), None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"[:200]


def summarize_place(p: dict) -> dict:
    reviews = p.get("reviews") or []
    dates = []
    for rv in reviews:
        try:
            dates.append(datetime.fromisoformat(rv["publishTime"].replace("Z", "+00:00")))
        except Exception:
            pass
    newest = max(dates) if dates else None
    city = region = None
    for comp in p.get("addressComponents") or []:
        if "locality" in comp.get("types", []):
            city = comp.get("longText")
        if "administrative_area_level_1" in comp.get("types", []):
            region = comp.get("shortText")
    return {
        "place_id": p.get("id"),
        "name": (p.get("displayName") or {}).get("text"),
        "address": p.get("formattedAddress"),
        "city": city, "region": region,
        "phone": p.get("nationalPhoneNumber"),
        "website": p.get("websiteUri"),
        "rating": p.get("rating"),
        "review_count": p.get("userRatingCount") or 0,
        "has_hours": bool((p.get("regularOpeningHours") or {}).get("weekdayDescriptions")),
        "photo_count": len(p.get("photos") or []),   # API returns at most 10
        "status": p.get("businessStatus"),
        "primary_type": p.get("primaryType"),
        "category": (p.get("primaryTypeDisplayName") or {}).get("text"),
        "maps_url": p.get("googleMapsUri"),
        "location": p.get("location"),
        "newest_review_days": (datetime.now(timezone.utc) - newest).days if newest else None,
        "review_sample_count": len(reviews),
    }


def match_place(places: list[dict], site_domain: str, phone: str, name: str) -> tuple[dict | None, str]:
    """Pick the listing that is really this business. Website match beats phone beats name."""
    for p in places:
        if p.get("websiteUri") and domain_of(p["websiteUri"]) == site_domain:
            return p, "website"
    if digits(phone):
        for p in places:
            if digits(p.get("nationalPhoneNumber")) == digits(phone):
                return p, "phone"
    want = name_tokens(name)
    if want:
        best, best_score = None, 0.0
        for p in places:
            got = name_tokens((p.get("displayName") or {}).get("text", ""))
            if not got:
                continue
            score = len(want & got) / len(want)
            if score > best_score:
                best, best_score = p, score
        if best and best_score >= 0.6:
            return best, "name"
    return None, ""


async def find_business(client, name: str, location: str, site_domain: str, phone: str):
    queries = [q for q in [f"{name} {location}".strip(), name, site_domain] if q]
    tried_err = None
    for q in dict.fromkeys(queries):
        places, err = await places_search(client, q, max_results=8)
        if err:
            tried_err = err
            if err == "not configured":
                return None, "", err
            continue
        p, how = match_place(places, site_domain, phone, name)
        if p:
            return summarize_place(p), how, None
    return None, "", tried_err


async def competitors(client, category: str, city: str, center: dict | None, self_place_id: str | None,
                      self_domain: str, n: int):
    """Google Maps results for '<category> in <city>' — our stand-in for the local map pack."""
    query = f"{category} in {city}".strip()
    places, err = await places_search(client, query, center=center, max_results=20)
    if err:
        return {"query": query, "error": err, "rank": None, "results": [], "competitors": []}
    results = [summarize_place(p) for p in places]
    rank = None
    for i, r in enumerate(results, 1):
        if (self_place_id and r["place_id"] == self_place_id) or (r["website"] and domain_of(r["website"]) == self_domain):
            rank = i
            break
    comps = [r for r in results
             if r["place_id"] != self_place_id and not (r["website"] and domain_of(r["website"]) == self_domain)
             and r.get("status") in (None, "OPERATIONAL")]
    # Prefer competitors with a website so we can compare sites, but keep Maps order
    with_site = [c for c in comps if c.get("website")]
    chosen = (with_site + [c for c in comps if not c.get("website")])[:n]
    chosen.sort(key=lambda c: results.index(c))
    for c in chosen:
        c["maps_rank"] = results.index(c) + 1
    return {"query": query, "error": None, "rank": rank, "results_count": len(results),
            "results": results[:10], "competitors": chosen}


async def pagespeed(client: httpx.AsyncClient, url: str) -> dict:
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if config.GOOGLE_API_KEY:
        params["key"] = config.GOOGLE_API_KEY
    try:
        r = await client.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                             params=params, timeout=90)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        lh = r.json().get("lighthouseResult", {})
        audits = lh.get("audits", {})
        score = lh.get("categories", {}).get("performance", {}).get("score")
        return {
            "mobile_score": round(score * 100) if score is not None else None,
            "lcp": audits.get("largest-contentful-paint", {}).get("displayValue"),
            "cls": audits.get("cumulative-layout-shift", {}).get("displayValue"),
            "tbt": audits.get("total-blocking-time", {}).get("displayValue"),
            "fcp": audits.get("first-contentful-paint", {}).get("displayValue"),
            "error": None,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}"}


async def youtube_channel(client: httpx.AsyncClient, url: str | None) -> dict:
    if not url:
        return {"found": False}
    if not config.GOOGLE_API_KEY:
        return {"found": True, "url": url, "error": "not configured"}
    m = re.search(r"youtube\.com/(?:(channel)/([\w-]+)|@([\w.-]+)|(?:c|user)/([\w.-]+))", url)
    if not m:
        return {"found": True, "url": url, "error": "unrecognized channel URL"}
    params = {"part": "snippet,statistics,contentDetails", "key": config.GOOGLE_API_KEY}
    if m.group(1):
        params["id"] = m.group(2)
    elif m.group(3):
        params["forHandle"] = "@" + m.group(3)
    else:
        params["forUsername"] = m.group(4)
    try:
        r = await client.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=15)
        items = r.json().get("items", []) if r.status_code == 200 else []
        if not items:
            return {"found": True, "url": url, "error": "channel not resolved"}
        ch = items[0]
        stats = ch.get("statistics", {})
        uploads = ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        last_days = None
        if uploads:
            r2 = await client.get("https://www.googleapis.com/youtube/v3/playlistItems", timeout=15, params={
                "part": "contentDetails", "playlistId": uploads, "maxResults": 1, "key": config.GOOGLE_API_KEY})
            its = r2.json().get("items", []) if r2.status_code == 200 else []
            if its:
                pub = its[0]["contentDetails"].get("videoPublishedAt")
                if pub:
                    last_days = (datetime.now(timezone.utc) - datetime.fromisoformat(pub.replace("Z", "+00:00"))).days
        return {"found": True, "url": url, "title": ch["snippet"].get("title"),
                "subscribers": int(stats.get("subscriberCount", 0) or 0),
                "videos": int(stats.get("videoCount", 0) or 0), "last_upload_days": last_days, "error": None}
    except Exception as e:
        return {"found": True, "url": url, "error": type(e).__name__}
