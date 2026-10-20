"""The audit pipeline: gather → measure → score → narrate. Each stage degrades gracefully."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from . import config, narrative, scoring
from .checks import ai_answers, brand, competitors as comp_mod, google, schema as schema_mod, site as site_mod
from .crawl import discover
from .fetch import Browser, domain_of, normalize_url, visible_text

log = logging.getLogger("audit")


def _split_location(loc: str) -> tuple[str, str]:
    parts = [p.strip() for p in (loc or "").split(",") if p.strip()]
    if not parts:
        return "", ""
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _phones(text: str) -> list[str]:
    found = re.findall(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}", text or "")
    out = []
    for f in found:
        d = google.digits(f)
        if len(d) == 10 and d not in [google.digits(o) for o in out]:
            out.append(f.strip())
    return out[:5]


async def run(req: dict, progress=None, transport=None) -> dict:
    t0 = time.time()

    def step(msg):
        log.info("[%s] %s", req.get("url"), msg)
        if progress:
            progress(msg)

    url = normalize_url(req["url"])
    name_in = (req.get("business_name") or "").strip()
    city_in, region_in = _split_location(req.get("location", ""))
    timings: dict[str, float] = {}

    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=30), transport=transport) as client:
        # ── 1. Site discovery + Google listing in parallel
        step("Scanning website and Google listing")
        site_task = asyncio.create_task(discover(client, url))
        gbp_task = asyncio.create_task(google.find_business(
            client, name_in or domain_of(url), req.get("location", ""), domain_of(url), req.get("phone", "")))
        site = await site_task
        gbp, gbp_how, gbp_err = await gbp_task
        timings["discover"] = round(time.time() - t0, 1)

        # ── 2. Real browser render (brand + JS gap), only a few pages
        step("Rendering pages like a customer would")
        rendered_home, rendered_more = None, {}
        try:
            async with Browser() as b:
                rendered_home = await b.render(site.home_chrome.final_url or url if site.home_chrome else url,
                                               collect_brand=True)
                extra = [u for k in ("about", "team") for u in site.page_kinds.get(k, [])[:1]]
                for u in list(dict.fromkeys(extra))[:2]:
                    rp = await b.render(u)
                    if rp.ok:
                        rendered_more[u] = rp
        except Exception as e:
            log.warning("browser failed: %s", e)
        timings["render"] = round(time.time() - t0, 1)

        home_raw_ok = bool(site.home_chrome and site.home_chrome.ok)
        home_render_ok = bool(rendered_home and rendered_home.ok)
        gpt_walled = bool(site.home_gptbot and site.home_gptbot.walled)
        chrome_walled = bool(site.home_chrome and site.home_chrome.walled)
        scan = {"home_ok": home_raw_ok or home_render_ok, "raw_ok": home_raw_ok, "render_ok": home_render_ok,
                "gptbot_walled_only": gpt_walled and not chrome_walled and home_raw_ok, "note": None}
        if not scan["home_ok"]:
            if chrome_walled or (rendered_home and rendered_home.walled):
                scan["note"] = ("Your site's bot protection blocked our scanner, so website checks are marked "
                                "'not measured' rather than scored. That same protection may be blocking AI crawlers.")
            else:
                err = (site.home_chrome.error if site.home_chrome else None) or (rendered_home.error if rendered_home else None)
                scan["note"] = f"We couldn't load the website ({err or 'no response'}), so website checks are not measured."

        # Text corpus (prefer rendered where we have it)
        texts: dict[str, str] = {}
        htmls: dict[str, str] = {}
        for u, p in site.pages.items():
            htmls[u] = p.html
            texts[u] = visible_text(p.html)
        if home_render_ok:
            hu = site.home_chrome.final_url if site.home_chrome and site.home_chrome.final_url else url
            texts[hu] = rendered_home.text or visible_text(rendered_home.html)
            htmls[hu] = rendered_home.html
        for u, rp in rendered_more.items():
            texts[u] = rp.text
            htmls[u] = rp.html

        # ── 3. Identity & brand
        step("Capturing brand and identity")
        identity, id_err = ({}, "site not scanned")
        if texts:
            identity, id_err = await brand.identity(name_in, url, req.get("location", ""), texts, config.AI_QUERY_COUNT)
        name = name_in or (identity.get("business_name") or {}).get("value") or (gbp or {}).get("name") or site.domain
        if not gbp and not name_in and name and gbp_err != "not configured":
            gbp, gbp_how, gbp_err = await google.find_business(client, name, req.get("location", ""), site.domain,
                                                               req.get("phone", ""))
        city = (gbp or {}).get("city") or identity.get("city") or city_in
        region = (gbp or {}).get("region") or identity.get("region") or region_in
        category = identity.get("category") or (gbp or {}).get("category") or "local business"

        sch_all = schema_mod.merge([schema_mod.extract(h) for h in htmls.values()])
        sch = schema_mod.analyze(sch_all)
        schema_logo = None
        if sch.get("best_local"):
            lg = sch["best_local"].get("logo") or sch["best_local"].get("image")
            schema_logo = lg.get("url") if isinstance(lg, dict) else (lg[0] if isinstance(lg, list) and lg else lg)
            schema_logo = schema_logo if isinstance(schema_logo, str) else None
        brand_raw = (rendered_home.brand if home_render_ok else {}) or {}
        brand_out = {
            "palette": brand.palette(brand_raw) if brand_raw else None,
            "fonts": brand.fonts(brand_raw) if brand_raw else None,
            "logo": await brand.logo(client, brand_raw, site.base, schema_logo) if (brand_raw or schema_logo) else
                    {"url": None, "data_uri": None, "source": None},
            "tagline": (identity.get("tagline") or {}).get("value"),
            "mission": identity.get("mission"),
            "vision": identity.get("vision"),
            "values": identity.get("values") or [],
            "leadership": identity.get("leadership") or [],
            "founded_year": (identity.get("founded_year") or {}).get("value"),
            "services": identity.get("services") or [],
            "service_areas": identity.get("service_areas") or [],
            "voice": identity.get("voice"),
            "error": id_err,
        }

        # ── 4. Google data, speed, competitors
        step("Checking Google, speed and local competitors")
        center = (gbp or {}).get("location")
        ps_task = asyncio.create_task(google.pagespeed(client, url)) if scan["home_ok"] else None
        comp_res = await google.competitors(client, category, f"{city}{', ' + region if region else ''}", center,
                                            (gbp or {}).get("place_id"), site.domain, config.COMPETITOR_COUNT) \
            if city else {"error": "No city to search", "competitors": []}
        comp_sites = await asyncio.gather(*(comp_mod.light_site(client, c["website"]) if c.get("website") else
                                            asyncio.sleep(0, result={"scanned": False, "reason": "no website"})
                                            for c in comp_res.get("competitors", [])))
        for c, s in zip(comp_res.get("competitors", []), comp_sites):
            c["site"] = s
        pagespeed = await ps_task if ps_task else {"error": "site not scanned"}
        social = site_mod.social_links(htmls, sch.get("same_as", []), {
            "facebook": req.get("facebook_url", ""), "instagram": req.get("instagram_url", ""),
            "linkedin": req.get("linkedin_url", ""), "youtube": req.get("youtube_url", "")})
        yt = await google.youtube_channel(client, social.get("youtube"))
        timings["google"] = round(time.time() - t0, 1)

        # ── 5. AI answers
        step("Asking ChatGPT, Gemini, Perplexity and Claude")
        questions = identity.get("buyer_questions") or []
        if not questions and city:
            questions = [f"Who is the best {category} in {city}?",
                         f"Can you recommend a {category} near {city}{', ' + region if region else ''}?",
                         f"What are the top-rated {category} companies in {city}?"]
        site_facts = "\n".join(f"- {k}: {v}" for k, v in {
            "name": name, "website": site.domain, "city": city, "phone": identity.get("phone") or (gbp or {}).get("phone"),
            "address": identity.get("address") or (gbp or {}).get("address"),
            "services": ", ".join(brand_out["services"][:8]), "category": category}.items() if v)
        comp_names = [c["name"] for c in comp_res.get("competitors", []) if c.get("name")]
        ai = await ai_answers.run(name, site.domain, city, region, questions, site_facts, comp_names) \
            if questions else {"engines": [], "error": "Could not determine buyer questions"}
        timings["ai"] = round(time.time() - t0, 1)

    # ── 6. Assemble measurements
    rb = site_mod.robots_access(site.robots_txt, site.home_chrome.final_url if site.home_chrome and site.home_chrome.final_url else url) \
        if (site.robots_status is not None or scan["home_ok"]) else None
    js = site_mod.js_dependency(site.home_gptbot.html if site.home_gptbot and site.home_gptbot.ok else
                                (site.home_chrome.html if site.home_chrome else ""),
                                rendered_home.text if home_render_ok else "", name,
                                req.get("phone") or (gbp or {}).get("phone") or "") if home_render_ok else None
    meta = site_mod.meta_flags(site.home_chrome.html if home_raw_ok else (rendered_home.html if home_render_ok else ""),
                               site.home_chrome.headers if site.home_chrome else {})
    content = site_mod.content_shape(site, texts, htmls)
    site_phones = _phones(" ".join(texts.values())) if scan["home_ok"] else None
    gphone = (gbp or {}).get("phone")
    nap = {"gbp_phone": gphone, "site_phones": site_phones,
           "match": bool(gphone and site_phones and google.digits(gphone) in [google.digits(p) for p in site_phones])}

    ai_sov = ai.get("competitor_sov") or {}
    for c in comp_res.get("competitors", []):
        c["ai_sov"] = ai_sov.get(c["name"])
        c["comparable_score"] = comp_mod.comparable_score(c, c["ai_sov"] if ai.get("answers_total") else None)
    you = {"review_count": (gbp or {}).get("review_count"), "rating": (gbp or {}).get("rating"),
           "site": {"scanned": scan["home_ok"], "local_schema": sch["has_local"], "faq_schema": sch["has_faq"],
                    "ai_bots_allowed_n": sum(1 for v in (rb or {}).get("search", {}).values() if v),
                    "raw_text_chars": (js or {}).get("raw_chars", 0), "llms_txt": bool(site.llms_txt)}}
    comp_res["you_comparable_score"] = comp_mod.comparable_score(you, ai.get("share_of_voice") if ai.get("answers_total") else None)

    D = {
        "scan": scan, "js": js, "robots": rb, "llms": bool(site.llms_txt), "sitemap_found": site.sitemap_found,
        "meta": meta, "https": (site.home_chrome.final_url or url).startswith("https") if site.home_chrome else url.startswith("https"),
        "schema": sch, "pagespeed": pagespeed, "nap": nap, "content": content, "identity": identity,
        "gbp": gbp, "gbp_error": gbp_err, "gbp_match_how": gbp_how, "market": comp_res, "ai": ai,
        "social": social, "youtube": yt,
    }
    checks, scores = scoring.score_all(D)

    sch_public = {k: v for k, v in sch.items() if k not in ("best_local", "persons")}
    result = {
        "version": config.VERSION,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "business": {"name": name, "url": url, "domain": site.domain, "city": city, "region": region,
                     "category": category, "contact_name": req.get("contact_name", ""), "email": req.get("email", ""),
                     "phone": req.get("phone", ""), "challenge": req.get("challenge", "")},
        "scan": scan,
        "scores": scores,
        "brand": brand_out,
        "ai": {k: v for k, v in ai.items()},
        "market": comp_res,
        "google": {"profile": gbp, "matched_by": gbp_how, "error": gbp_err},
        "site": {"js": js, "robots": rb, "llms_txt": bool(site.llms_txt), "sitemap_found": site.sitemap_found,
                 "sitemap_url_count": len(site.sitemap_urls), "pages_scanned": sorted(texts.keys())[:30],
                 "schema": sch_public, "meta": meta, "pagespeed": pagespeed, "content": content, "nap": nap,
                 "social": social, "youtube": yt},
        "unscored": {
            "streaming_tv": "We can't see streaming-TV advertising from outside, and very few local businesses run it yet. "
                            "It's the open lane: TV-quality reach in your exact service area, measured like digital.",
            "inside_google": "Search Console and Analytics show what Google itself sees: indexed pages, crawl errors, the "
                             "searches you actually appear for. Connect Google to unlock this section.",
        },
        "timings": timings,
    }
    step("Writing the report")
    result["narrative"] = await narrative.write(result)
    result["timings"]["total"] = round(time.time() - t0, 1)
    return result
