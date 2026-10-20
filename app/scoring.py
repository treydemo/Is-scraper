"""Deterministic scoring. Same inputs, same score, every time. Claude never touches a number."""
from __future__ import annotations

from .checks.site import SEARCH_BOTS, TRAINING_BOTS
from .model import INFO, Check, roll_up


def _c(id, section, label, points, sells, fix=""):
    return Check(id=id, section=section, label=label, points=points, sells=sells, fix=fix)


def ai_answers(D: dict) -> list[Check]:
    ai = D.get("ai") or {}
    S, sells = "ai_answers", "Immersive Authority System"
    sov = _c("ai_sov", S, "Named when buyers ask AI for a recommendation", 15, sells,
             "Build the entity signals AI engines rely on (consistent listings, answer content, reviews, citations) so they have a reason to name you.")
    breadth = _c("ai_engines", S, "Shows up across multiple AI engines", 5, sells,
                 "Each engine pulls from different sources; you need coverage across Google, Bing and the open web, not just one.")
    know = _c("ai_brand", S, "AI describes the business accurately", 5, sells,
              "Publish clear, machine-readable facts about who you are, what you do and where, so AI stops guessing.")
    if not ai or not ai.get("engines"):
        why = (ai or {}).get("error") or "AI engines not configured"
        return [sov.skip(why), breadth.skip(why), know.skip(why)]
    total, named = ai["answers_total"], ai["answers_named"]
    if not total:
        return [sov.skip("No AI engine returned an answer"), breadth.skip("No answers"), know.skip("No answers")]
    pct = ai["share_of_voice"]
    sov.score(15 * min(1.0, pct / 50),
              f"Named in {named} of {total} AI answers ({pct}%) to buyer questions across {', '.join(ai['engines'])}.")
    engines_named = [e for e, v in ai["by_engine"].items() if v["named"] > 0]
    n_eng = len([e for e, v in ai["by_engine"].items() if v["asked"]])
    breadth.score(5 * (len(engines_named) / n_eng) if n_eng else 0,
                  f"Named by {len(engines_named)} of {n_eng} engines" +
                  (f": {', '.join(engines_named)}." if engines_named else "."))
    verdicts = ai.get("brand_verdicts") or []
    if verdicts:
        pts_map = {"accurate": 1.0, "partly_wrong": 0.5, "no_info": 0.15, "wrong": 0.0}
        avg = sum(pts_map.get(v.get("accuracy"), 0) for v in verdicts) / len(verdicts)
        know.score(5 * avg, "; ".join(f"{v.get('engine')}: {v.get('accuracy', '?').replace('_', ' ')}" for v in verdicts) + ".")
    else:
        know.skip("Brand-knowledge check unavailable")
    return [sov, breadth, know]


def ai_readable(D: dict) -> list[Check]:
    S, sells = "ai_readable", "Authority Site"
    out = []
    scan_ok = D["scan"]["home_ok"]

    c = _c("js_dependency", S, "AI crawlers can read the page without running JavaScript", 5, sells,
           "Serve the real content in the HTML (server-side render or static), so ChatGPT, Claude and Perplexity crawlers see what humans see.")
    js = D.get("js")
    if D["scan"].get("gptbot_walled_only"):
        c.score(0, "Your firewall served AI crawlers a challenge page instead of your site, while a normal browser got through. "
                   "AI search bots likely can't read you at all.",
                "Allow verified AI search crawlers through your bot protection (Cloudflare 'AI bots' / WAF rules).")
    elif not scan_ok or not js or js.get("ratio") is None:
        c.skip(D["scan"].get("note") or "Homepage could not be scanned")
    else:
        r = js["ratio"]
        earned = 5 if r >= 0.8 else 3.5 if r >= 0.6 else 2 if r >= 0.35 else 0.5 if r >= 0.1 else 0
        if not js["name_in_raw"]:
            earned = min(earned, 2)
        c.score(earned, f"AI crawlers see {round(r * 100)}% of the homepage text a human sees "
                        f"({js['raw_chars']:,} of {js['rendered_chars']:,} characters)"
                        + ("" if js["name_in_raw"] else "; your business name isn't even in the crawler's copy") + ".")
    out.append(c)

    c = _c("ai_bot_access", S, "AI search bots are allowed in robots.txt", 4, sells,
           "Update robots.txt to allow AI search crawlers (OAI-SearchBot, PerplexityBot, Claude-SearchBot, Bingbot).")
    rb = D.get("robots")
    if not scan_ok:
        c.skip(D["scan"].get("note") or "Site could not be scanned")
        rb = None
    elif rb is None:
        c.skip("robots.txt not checked")
    else:
        blocked = [f"{SEARCH_BOTS[b]} ({b})" for b, ok in rb["search"].items() if not ok]
        allowed = len(SEARCH_BOTS) - len(blocked)
        ev = (f"{allowed} of {len(SEARCH_BOTS)} AI/search crawlers allowed." +
              (f" Blocked: {', '.join(blocked)}." if blocked else "") +
              ("" if rb["has_robots"] else " No robots.txt file (everything allowed by default)."))
        c.score(4 * allowed / len(SEARCH_BOTS), ev)
    out.append(c)

    c = _c("training_bots", S, "AI training crawlers (informational)", 0, sells)
    if rb is not None:
        blocked_t = [TRAINING_BOTS[b] for b, ok in rb["training"].items() if not ok]
        c.status = INFO
        c.evidence = ("Blocks training crawlers: " + ", ".join(blocked_t) + ". That's a policy choice, but it limits "
                      "what AI models learn about you between searches.") if blocked_t else "Training crawlers allowed."
    else:
        c.skip("robots.txt not checked")
    out.append(c)

    sch = D.get("schema")
    c = _c("schema_local", S, "LocalBusiness structured data, complete", 3, sells,
           "Add complete LocalBusiness (or your specific subtype) schema: name, address, phone, URL, hours, geo, logo, sameAs.")
    c2 = _c("schema_breadth", S, "Rich structured data (Organization, Service, FAQ, Review, Person)", 2, sells,
            "Mark up services, FAQs, reviews and your people so AI can quote them as facts.")
    if not scan_ok or sch is None:
        c.skip(D["scan"].get("note") or "Not scanned")
        c2.skip(D["scan"].get("note") or "Not scanned")
    else:
        if sch["has_local"]:
            miss_req = sch["local_missing_required"]
            miss_rec = sch["local_missing_recommended"]
            earned = 3 - 0.75 * len(miss_req) - 0.2 * len(miss_rec)
            ev = f"{sch['local_type']} schema found."
            if miss_req:
                ev += f" Missing required: {', '.join(miss_req)}."
            if miss_rec:
                ev += f" Missing recommended: {', '.join(miss_rec)}."
            c.score(max(0.5, earned), ev)
        else:
            c.score(0, "No LocalBusiness schema on the pages scanned" +
                    (f" (found: {', '.join(sch['types'][:6])})." if sch["types"] else " — no structured data at all."))
        flags = {"Organization": sch["has_org"], "Service": sch["has_service"], "FAQPage": sch["has_faq"],
                 "Review/Rating": sch["has_review"], "Person": sch["has_person"], "sameAs links": bool(sch["same_as"])}
        have = [k for k, v in flags.items() if v]
        c2.score(2 * len(have) / len(flags),
                 f"Has {', '.join(have) if have else 'none'}; missing {', '.join(k for k, v in flags.items() if not v) or 'nothing'}."
                 + (f" {sch['errors']} schema block(s) failed to parse." if sch["errors"] else ""))
    out += [c, c2]

    c = _c("sitemap", S, "XML sitemap", 1, sells, "Publish an XML sitemap and reference it in robots.txt.")
    if not scan_ok:
        c.skip(D["scan"].get("note") or "Site could not be scanned")
    else:
      c.score(1 if D.get("sitemap_found") else 0,
            f"Sitemap found with {D['content'].get('sitemap_url_count', 0)} URLs." if D.get("sitemap_found") else "No XML sitemap found.")
    out.append(c)

    c = _c("llms_txt", S, "llms.txt / AI instructions file", 1, sells,
           "Publish an llms.txt that tells AI systems who you are and which pages matter. Low effort, emerging standard.")
    if not scan_ok:
        c.skip(D["scan"].get("note") or "Site could not be scanned")
    else:
      c.score(1 if D.get("llms") else 0, "llms.txt present." if D.get("llms") else "No llms.txt file.")
    out.append(c)

    c = _c("speed", S, "Mobile speed (Google PageSpeed)", 2, sells,
           "Compress images, cut render-blocking scripts, and host on fast infrastructure.")
    ps = D.get("pagespeed") or {}
    if ps.get("mobile_score") is None:
        c.skip("PageSpeed unavailable" + (f" ({ps.get('error')})" if ps.get("error") else ""))
    else:
        s = ps["mobile_score"]
        c.score(2 if s >= 85 else 1.5 if s >= 65 else 1 if s >= 45 else 0.5 if s >= 25 else 0,
                f"Mobile performance {s}/100 (LCP {ps.get('lcp') or '?'}).")
    out.append(c)

    c = _c("indexable", S, "HTTPS and indexable", 1, sells, "Serve over HTTPS and remove noindex from public pages.")
    meta = D.get("meta") or {}
    if not scan_ok:
        c.skip(D["scan"].get("note") or "Not scanned")
    else:
        bad = []
        if not D.get("https"):
            bad.append("not on HTTPS")
        if meta.get("noindex"):
            bad.append("homepage is set to noindex")
        c.score(0 if bad else 1, "; ".join(bad).capitalize() + "." if bad else "HTTPS, indexable homepage.")
    out.append(c)

    c = _c("nap", S, "Phone number consistent between website and Google", 1, sells,
           "Use one phone number everywhere: website, Google Business Profile, directories, schema.")
    nap = D.get("nap") or {}
    if nap.get("gbp_phone") and nap.get("site_phones") is not None:
        ok = nap.get("match")
        c.score(1 if ok else 0, f"Google lists {nap['gbp_phone']}; website " +
                ("shows the same number." if ok else
                 (f"shows {', '.join(nap['site_phones'][:2])}." if nap["site_phones"] else "shows no phone number we could read.")))
    else:
        c.skip("Needs both a scanned site and a Google listing")
    out.append(c)
    return out


def content(D: dict) -> list[Check]:
    S, sells = "content", "Knowledge Hub"
    cs = D.get("content") or {}
    idn = D.get("identity") or {}
    sch = D.get("schema") or {}
    scan_ok = D["scan"]["home_ok"]
    checks = [
        _c("faq", S, "FAQ content AI can quote", 3, sells,
           "Publish real customer questions with direct answers (and FAQPage schema). This is the single most-quoted format in AI answers."),
        _c("questions", S, "Pages organized around the questions buyers ask", 2, sells,
           "Write headings as the questions customers actually ask, answered in the first sentence below."),
        _c("service_pages", S, "A dedicated page for each service", 2, sells,
           "Give every service its own page with what it is, who it's for, price range, and FAQs."),
        _c("area_pages", S, "Service-area / location pages", 2, sells,
           "Create a page per city or area you serve so AI can match you to 'near me' questions there."),
        _c("proof", S, "Case studies, testimonials or results", 2, sells,
           "Publish case studies and reviews on your own site. AI trusts first-party proof it can cite."),
        _c("freshness", S, "Content updated recently", 2, sells,
           "Publish something new every month. Freshness is a ranking and citation signal."),
        _c("about", S, "About page with the story and people behind the business", 1, sells,
           "Tell your story and introduce the people. AI answers 'who runs this business' from this page."),
        _c("identity", S, "Mission, vision or values published", 1, sells,
           "State your mission and values on the site in plain words; it's how AI describes what you stand for."),
    ]
    if not scan_ok:
        for c in checks:
            c.skip(D["scan"].get("note") or "Site could not be scanned")
        return checks
    faq, q, sp, ap, pr, fr, ab, idc = checks
    n_q = cs.get("question_heading_count", 0)
    faq.score(3 if (sch.get("has_faq") or cs.get("faq_page")) and n_q >= 3 else 2 if (sch.get("has_faq") or cs.get("faq_page") or n_q >= 5) else 1 if n_q >= 2 else 0,
              ("FAQ page found. " if cs.get("faq_page") else "No FAQ page found. ") +
              ("FAQPage schema present. " if sch.get("has_faq") else "") + f"{n_q} question-style headings across scanned pages.")
    q.score(2 if n_q >= 8 else 1.5 if n_q >= 5 else 1 if n_q >= 2 else 0,
            f"{n_q} headings are written as questions" + (f", e.g. “{cs['question_headings'][0]}”" if n_q else "") + ".")
    n_sp = cs.get("service_pages", 0)
    sp.score(2 if n_sp >= 4 else 1.5 if n_sp >= 2 else 0.75 if n_sp == 1 else 0,
             f"{n_sp} service page(s) found" + (f"; services named on site: {', '.join(idn.get('services', [])[:5])}." if idn.get("services") else "."))
    n_ap = cs.get("area_pages", 0)
    areas = idn.get("service_areas") or []
    ap.score(2 if n_ap >= 3 else 1.25 if n_ap >= 1 else 0.5 if areas else 0,
             f"{n_ap} area/location page(s)" + (f"; areas mentioned: {', '.join(areas[:6])}." if areas else "; no service areas named."))
    pr.score(2 if cs.get("proof_pages") else 1 if cs.get("testimonial_signals") or sch.get("has_review") else 0,
             ("Dedicated proof page(s) found." if cs.get("proof_pages") else
              "Testimonials appear on the site but no case-study/results page." if cs.get("testimonial_signals") else
              "No case studies or testimonials found on the site."))
    days = cs.get("newest_update_days")
    if days is None:
        fr.score(1 if cs.get("blog") else 0, "No dated sitemap entries; " + ("a blog exists." if cs.get("blog") else "no blog found."))
    else:
        fr.score(2 if days <= 45 else 1.25 if days <= 120 else 0.5 if days <= 365 else 0,
                 f"Most recent update: {days} days ago.")
    leaders = idn.get("leadership") or []
    ab.score(1 if cs.get("about_page") and leaders else 0.5 if cs.get("about_page") or leaders else 0,
             ("About page found. " if cs.get("about_page") else "No real About page. ") +
             (f"Leadership named: {', '.join(p['name'] for p in leaders[:4])}." if leaders else "No leadership named on the site."))
    have = [k for k in ("mission", "vision") if idn.get(k)] + (["values"] if idn.get("values") else [])
    idc.score(1 if len(have) >= 2 else 0.5 if have else 0,
              f"Published: {', '.join(have)}." if have else "No mission, vision or values stated on the pages scanned.")
    return checks


def google(D: dict) -> list[Check]:
    S, sells = "google", "Distribution Engine (GBP)"
    g = D.get("gbp")
    err = D.get("gbp_error")
    checks = [
        _c("gbp_found", S, "Google Business Profile found and linked to the website", 3, sells,
           "Claim and verify your Google Business Profile and link it to your website."),
        _c("gbp_rating", S, "Google rating", 3, sells, "Ask every happy customer for a review; respond to every review."),
        _c("gbp_reviews", S, "Review volume", 3, sells, "Run a steady review-request system; volume is a top local ranking factor."),
        _c("gbp_recent", S, "Recent reviews (review velocity)", 2, sells, "Keep reviews coming every week, not in bursts."),
        _c("gbp_hours", S, "Hours listed", 1, sells, "Add complete business hours, including special hours."),
        _c("gbp_photos", S, "Photos on the profile", 1, sells, "Upload fresh photos monthly: team, work, location."),
        _c("gbp_category", S, "Specific primary category", 1, sells, "Choose the most specific primary category Google offers."),
        _c("gbp_posts", S, "Active Google posts & Q&A", 1, sells, "Post weekly updates and offers to your profile."),
    ]
    if err == "not configured":
        for c in checks:
            c.skip("Google Places not configured")
        return checks
    found, rating, reviews, recent, hours, photos, cat, posts = checks
    posts.skip("Visible only from inside your Google account — unlocked when you connect Google")
    if not g:
        found.score(0, "We couldn't find a Google Business Profile matching your name, phone or website.")
        for c in (rating, reviews, recent, hours, photos, cat):
            c.skip("No Google Business Profile found")
        return checks
    linked = D.get("gbp_match_how") == "website"
    found.score(3 if linked else 2, f"Found “{g['name']}”" + (" linked to your website." if linked else
                f" (matched by {D.get('gbp_match_how')}, but the listing doesn't point to your website)."))
    r = g.get("rating")
    rating.score(0 if r is None else 3 if r >= 4.7 else 2.5 if r >= 4.5 else 2 if r >= 4.2 else 1 if r >= 3.8 else 0,
                 f"{r}★ average." if r is not None else "No rating yet.")
    n = g.get("review_count") or 0
    reviews.score(3 if n >= 150 else 2.5 if n >= 75 else 2 if n >= 40 else 1 if n >= 15 else 0.5 if n >= 5 else 0, f"{n} Google reviews.")
    d = g.get("newest_review_days")
    if d is None:
        recent.score(0, "No recent reviews visible.") if n == 0 else recent.skip("Review dates not returned by Google")
    else:
        recent.score(2 if d <= 14 else 1.5 if d <= 30 else 1 if d <= 60 else 0.5 if d <= 120 else 0,
                     f"Newest review we can see is {d} days old.")
    hours.score(1 if g.get("has_hours") else 0, "Hours listed." if g.get("has_hours") else "No hours on the profile.")
    pc = g.get("photo_count") or 0
    photos.score(1 if pc >= 10 else 0.5 if pc >= 3 else 0, f"{pc}{'+' if pc >= 10 else ''} photos.")
    generic = {"point_of_interest", "establishment", "store", "service", None}
    cat.score(0 if g.get("primary_type") in generic else 1,
              f"Primary category: {g.get('category') or g.get('primary_type') or 'none'}.")
    return checks


def market(D: dict) -> list[Check]:
    S, sells = "market", "Authority Engine"
    m = D.get("market") or {}
    checks = [
        _c("map_rank", S, "Position in Google Maps for your main service", 6, sells,
           "Map rank comes from relevance, distance and prominence: complete profile, reviews, local content and citations."),
        _c("reviews_vs", S, "Review count vs. top local competitors", 3, sells, "Close the review gap with a systematic review program."),
        _c("rating_vs", S, "Rating vs. top local competitors", 2, sells, "Respond to every review and resolve issues fast."),
        _c("ai_vs", S, "AI share of voice vs. the competitor AI recommends most", 4, sells,
           "Out-publish the competitor AI favors with answer content, citations and consistent entity data."),
    ]
    if not m or m.get("error"):
        why = (m or {}).get("error") or "Competitor lookup unavailable"
        for c in checks:
            c.skip("Google Places not configured" if why == "not configured" else why)
        return checks
    rank_c, rev_c, rat_c, ai_c = checks
    comps = m.get("competitors") or []
    rank = m.get("rank")
    rank_c.score(6 if rank == 1 else 5 if rank and rank <= 3 else 3 if rank and rank <= 6 else 1.5 if rank and rank <= 10 else 0,
                 f"Google Maps search “{m['query']}”: " + (f"you're #{rank} of {m.get('results_count')}." if rank else
                                                           f"you're not in the top {m.get('results_count', 20)} results."))
    g = D.get("gbp") or {}
    if comps:
        avg_rev = sum(c.get("review_count") or 0 for c in comps) / len(comps)
        mine = g.get("review_count") or 0
        rev_c.score(3 * min(1.0, mine / avg_rev) if avg_rev else 3,
                    f"You: {mine} reviews. Competitor average: {round(avg_rev)}.")
        rated = [c["rating"] for c in comps if c.get("rating")]
        if rated and g.get("rating"):
            avg_r = sum(rated) / len(rated)
            diff = g["rating"] - avg_r
            rat_c.score(2 if diff >= 0 else 1.5 if diff >= -0.2 else 0.75 if diff >= -0.5 else 0,
                        f"You: {g['rating']}★. Competitor average: {avg_r:.1f}★.")
        else:
            rat_c.skip("Ratings unavailable for comparison")
    else:
        rev_c.skip("No competitors found")
        rat_c.skip("No competitors found")
    ai = D.get("ai") or {}
    if ai.get("answers_total"):
        mine = ai.get("share_of_voice") or 0
        leaders = ai.get("top_named") or []
        best_other = max([x["pct"] for x in leaders if not x.get("is_target")] + [0])
        if best_other == 0:
            ai_c.score(4 if mine > 0 else 2, f"AI named you in {mine}% of answers; no single competitor dominates.")
        else:
            top = next(x for x in leaders if x["pct"] == best_other and not x.get("is_target"))
            ai_c.score(4 * min(1.0, mine / best_other),
                       f"AI named you in {mine}% of answers vs. {top['name']} at {best_other}%.")
    else:
        ai_c.skip("AI answers not measured")
    return checks


def distribution(D: dict) -> list[Check]:
    S, sells = "distribution", "Distribution Engine"
    soc = D.get("social") or {}
    yt = D.get("youtube") or {}
    scan_ok = D["scan"]["home_ok"]
    checks = [
        _c("social_profiles", S, "Social profiles linked from the website", 4, sells,
           "Link every active profile from your site and schema sameAs so AI connects them to you."),
        _c("youtube", S, "Active YouTube / video channel", 3, sells,
           "Publish short answer videos; YouTube is the #2 search engine and a heavy AI citation source."),
        _c("video_site", S, "Video on the website", 3, sells, "Embed video on key pages — it lifts engagement and trust."),
    ]
    sp, ytc, vs = checks
    core = [k for k in ("facebook", "instagram", "linkedin", "youtube", "tiktok", "x") if k in soc]
    if not scan_ok and not soc:
        sp.skip(D["scan"].get("note") or "Not scanned")
    else:
        sp.score(min(4, len(core) * 1.0), f"Profiles found: {', '.join(core) if core else 'none'}.")
    if not yt.get("found"):
        ytc.score(0, "No YouTube channel linked.")
    elif yt.get("error"):
        ytc.score(1, f"YouTube channel linked ({yt.get('url')}); activity couldn't be checked.")
    else:
        d = yt.get("last_upload_days")
        ytc.score(3 if d is not None and d <= 30 else 2 if d is not None and d <= 90 else 1,
                  f"“{yt.get('title')}”: {yt.get('videos', 0)} videos, {yt.get('subscribers', 0):,} subscribers"
                  + (f", last upload {d} days ago." if d is not None else "."))
    if not scan_ok:
        vs.skip(D["scan"].get("note") or "Not scanned")
    else:
        vs.score(3 if (D.get("content") or {}).get("video_on_site") else 0,
                 "Video embedded on the site." if (D.get("content") or {}).get("video_on_site") else "No video found on the pages scanned.")
    return checks


def score_all(D: dict) -> tuple[list[Check], dict]:
    checks = ai_answers(D) + ai_readable(D) + content(D) + google(D) + market(D) + distribution(D)
    return checks, roll_up(checks)
