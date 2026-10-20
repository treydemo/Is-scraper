"""A fake local business site + fake Google/AI responses for offline end-to-end tests."""
import json

BASE = "https://www.lonestark9.com"

HOME = """<!doctype html><html><head><title>Lone Star K9 Academy | Dog Training in Plano, TX</title>
<meta name="description" content="Obedience and behavior dog training in Plano.">
<link rel="canonical" href="https://www.lonestark9.com/">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"Lone Star K9 Academy",
"telephone":"(972) 555-0142","url":"https://www.lonestark9.com","address":{"@type":"PostalAddress","streetAddress":"100 Main St",
"addressLocality":"Plano","addressRegion":"TX"},"sameAs":["https://www.facebook.com/lonestark9"]}</script>
</head><body><header><img class="logo" src="/logo.png" alt="Lone Star K9 logo">
<nav><a href="/about">About</a><a href="/services/obedience">Obedience</a><a href="/services/puppy">Puppy Training</a>
<a href="/faq">FAQ</a><a href="/contact">Contact</a></nav></header>
<main><h1>Lone Star K9 Academy</h1><div id="app"></div></main>
<footer><a href="https://www.facebook.com/lonestark9">Facebook</a> <a href="https://www.instagram.com/lonestark9">IG</a>
<a href="https://www.youtube.com/@lonestark9">YouTube</a> Call (972) 555-0142</footer></body></html>"""

RENDERED_TEXT = ("Lone Star K9 Academy Dog training that sticks. Plano's obedience and behavior specialists. "
                 "We train dogs of every age with real-world obedience, off-leash reliability, and puppy foundations. "
                 "How long does training take? Most dogs finish in two weeks. Do you train aggressive dogs? Yes, with a "
                 "behavior assessment first. What does it cost? Programs start at $1,200. " * 6)

ABOUT = """<html><body><h1>About Lone Star K9 Academy</h1>
<p>Our mission is to give every Plano family a dog they can trust anywhere.</p>
<h2>Meet the team</h2><h3>Jake Morrison, Owner &amp; Head Trainer</h3><p>Jake has trained dogs in North Texas since 2012 and
has worked with hundreds of families across Collin County. He started the academy after years of seeing owners give up on dogs
that just needed structure, consistency, and a trainer who would show up.</p>
<h3>Why families choose us</h3><p>We keep classes small, we follow up after every program, and we teach owners as much as we teach dogs.
Every program ends with a real-world session at a park or store so you see the results where they matter.</p>
<iframe src="https://www.youtube.com/embed/abc"></iframe></body></html>"""

FAQ = """<html><body><h1>FAQ</h1><h2>How long does dog training take?</h2><p>Two weeks.</p>
<h2>Do you train aggressive dogs?</h2><p>Yes.</p><h2>How much does dog training cost in Plano?</h2><p>From $1,200.</p>
<h2>Do you offer puppy classes?</h2><p>Yes.</p></body></html>"""

SERVICE = "<html><body><h1>{name}</h1><p>{name} in Plano TX. Testimonial: what our clients say — 5 stars.</p></body></html>"

ROBOTS = """User-agent: GPTBot
Disallow: /

User-agent: PerplexityBot
Disallow: /

User-agent: *
Allow: /
Sitemap: https://www.lonestark9.com/sitemap.xml
"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.lonestark9.com/</loc><lastmod>2026-09-30</lastmod></url>
<url><loc>https://www.lonestark9.com/about</loc><lastmod>2026-05-01</lastmod></url>
<url><loc>https://www.lonestark9.com/services/obedience</loc></url>
<url><loc>https://www.lonestark9.com/services/puppy</loc></url>
<url><loc>https://www.lonestark9.com/faq</loc></url>
</urlset>"""

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082") * 20


def place(pid, name, site, phone, rating, count, city="Plano", lat=33.02, lng=-96.70, days_ago_iso="2026-10-10T12:00:00Z"):
    return {"id": pid, "displayName": {"text": name}, "formattedAddress": f"1 St, {city}, TX", "nationalPhoneNumber": phone,
            "websiteUri": site, "rating": rating, "userRatingCount": count,
            "regularOpeningHours": {"weekdayDescriptions": ["Mon: 9-5"]}, "photos": [{}] * 10,
            "businessStatus": "OPERATIONAL", "primaryType": "dog_trainer", "primaryTypeDisplayName": {"text": "Dog trainer"},
            "reviews": [{"publishTime": days_ago_iso}], "googleMapsUri": "https://maps.google.com/?cid=1",
            "location": {"latitude": lat, "longitude": lng},
            "addressComponents": [{"longText": city, "shortText": city, "types": ["locality"]},
                                  {"longText": "Texas", "shortText": "TX", "types": ["administrative_area_level_1"]}]}


SELF = place("p_self", "Lone Star K9 Academy", BASE + "/", "(972) 555-0142", 4.8, 37)
COMPS = [place("p_a", "Sit Means Sit Dog Training Plano", "https://sitmeanssit.com/plano", "(972) 555-1000", 4.9, 212),
         place("p_b", "Bark Busters Plano", "https://barkbusters.com", "(972) 555-2000", 4.7, 140),
         SELF,
         place("p_c", "Pawsitive Pups", None, "(972) 555-3000", 4.4, 30)]

COMP_HOME = """<html><head><script type="application/ld+json">{"@type":"LocalBusiness","name":"X"}</script></head>
<body><h2>Is dog training worth it?</h2><p>""" + ("Text " * 800) + "</p></body></html>"

PAGESPEED = {"lighthouseResult": {"categories": {"performance": {"score": 0.42}},
                                  "audits": {"largest-contentful-paint": {"displayValue": "6.1 s"}}}}


def ai_answer(engine, q):
    if "about Lone Star" in q:
        return "Lone Star K9 Academy is a dog trainer in Dallas offering grooming." if engine == "Gemini" else \
            "Lone Star K9 Academy in Plano offers obedience and puppy training; reviews are strong."
    if engine == "ChatGPT" and "puppy" in q.lower():
        return "Top picks: 1. Sit Means Sit Dog Training Plano 2. Lone Star K9 Academy 3. Bark Busters Plano"
    return "I'd recommend Sit Means Sit Dog Training Plano and Bark Busters Plano. Both are highly rated."


def handler(request):
    import httpx
    u = str(request.url)
    ua = request.headers.get("user-agent", "")
    if u.startswith("https://places.googleapis.com"):
        body = json.loads(request.content)
        if "in Plano" in body["textQuery"]:
            return httpx.Response(200, json={"places": COMPS})
        return httpx.Response(200, json={"places": [SELF]})
    if "pagespeedonline" in u:
        return httpx.Response(200, json=PAGESPEED)
    if "youtube/v3/channels" in u:
        return httpx.Response(200, json={"items": [{"snippet": {"title": "Lone Star K9"},
                                                    "statistics": {"subscriberCount": "120", "videoCount": "14"},
                                                    "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
    if "youtube/v3/playlistItems" in u:
        return httpx.Response(200, json={"items": [{"contentDetails": {"videoPublishedAt": "2026-08-01T00:00:00Z"}}]})
    if u.startswith("https://sitmeanssit.com") or u.startswith("https://barkbusters.com"):
        if u.endswith("robots.txt"):
            return httpx.Response(200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
        if u.endswith("llms.txt"):
            return httpx.Response(404, text="")
        return httpx.Response(200, text=COMP_HOME, headers={"content-type": "text/html"})
    if u.startswith(BASE) or u.startswith("https://lonestark9.com"):
        path = u.replace(BASE, "").replace("https://lonestark9.com", "") or "/"
        html = {"/": HOME, "/about": ABOUT, "/faq": FAQ,
                "/services/obedience": SERVICE.format(name="Obedience Training"),
                "/services/puppy": SERVICE.format(name="Puppy Training"),
                "/contact": "<html><body><h1>Contact</h1><p>(972) 555-0142</p></body></html>"}.get(path)
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS, headers={"content-type": "text/plain"})
        if path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP, headers={"content-type": "application/xml"})
        if path == "/logo.png":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if html:
            return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404, text="<html>404</html>", headers={"content-type": "text/html"})
    return httpx.Response(404, text="")
