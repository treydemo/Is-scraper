"""Brand capture: colors, fonts, logo (from the rendered page) and identity (mission, vision, values,
leadership, services) extracted from the site's own words. Nothing is invented: every extracted
statement must appear in the page text or it is dropped."""
from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from .. import llm


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _is_neutral(h: str) -> bool:
    r, g, b = _hex_to_rgb(h)
    return max(r, g, b) - min(r, g, b) < 18


def _lum(h: str) -> float:
    r, g, b = _hex_to_rgb(h)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _close(a: str, b: str) -> bool:
    ra, rb = _hex_to_rgb(a), _hex_to_rgb(b)
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5 < 40


def palette(brand_raw: dict) -> dict:
    colors = brand_raw.get("colors") or {}
    items = sorted(colors.items(), key=lambda kv: -kv[1]["weight"])
    chromatic, neutrals = [], []
    for hexv, meta in items:
        bucket = neutrals if _is_neutral(hexv) else chromatic
        if not any(_close(hexv, x["hex"]) for x in bucket):
            bucket.append({"hex": hexv, "weight": round(meta["weight"], 1), "roles": sorted(meta["roles"],
                           key=lambda r: -meta["roles"][r])[:3]})
    # Button / link colors are usually the true brand accent
    def role_score(c, role):
        return sum(1 for r in c["roles"] if r.startswith(role))
    primary = next((c for c in chromatic if role_score(c, "button") or role_score(c, "header")), None) \
        or (chromatic[0] if chromatic else None)
    accent = next((c for c in chromatic if c is not primary), None)
    background = next((n for n in neutrals if "body-bg" in n["roles"]), neutrals[0] if neutrals else None)
    text = next((n for n in neutrals if any(r.endswith("-text") for r in n["roles"]) and n is not background), None)
    return {
        "primary": primary["hex"] if primary else None,
        "accent": accent["hex"] if accent else None,
        "background": background["hex"] if background else None,
        "text": text["hex"] if text else None,
        "swatches": [c["hex"] for c in chromatic[:5]] + [n["hex"] for n in neutrals[:2]],
        "dark_theme": bool(background and _lum(background["hex"]) < 0.35),
    }


def fonts(brand_raw: dict) -> dict:
    f = brand_raw.get("fonts") or {}
    def top(k):
        d = f.get(k) or {}
        return max(d, key=d.get) if d else None
    families = set()
    for link in brand_raw.get("fontLinks") or []:
        q = parse_qs(urlparse(link).query)
        for fam in q.get("family", []):
            for part in fam.split("|"):
                families.add(part.split(":")[0].replace("+", " "))
    return {"heading": top("heading"), "body": top("body"), "button": top("button"),
            "web_font_families": sorted(families),
            "source": "Google Fonts" if any("googleapis" in l for l in brand_raw.get("fontLinks") or []) else
                      ("Adobe Fonts" if any("typekit" in l for l in brand_raw.get("fontLinks") or []) else None)}


async def logo(client: httpx.AsyncClient, brand_raw: dict, base: str, schema_logo: str | None) -> dict:
    cands = list(brand_raw.get("logo") or [])
    if schema_logo:
        cands.insert(1 if cands else 0, {"src": schema_logo, "how": "schema"})
    for c in cands:
        if c.get("src") == "inline-svg" and c.get("svg"):
            svg = c["svg"]
            if "xmlns" not in svg:
                svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
            return {"url": None, "source": c["how"],
                    "data_uri": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()}
        src = c.get("src")
        if not src or src.startswith("data:"):
            if src and src.startswith("data:image"):
                return {"url": None, "source": c["how"], "data_uri": src}
            continue
        url = urljoin(base + "/", src)
        try:
            r = await client.get(url, timeout=12, follow_redirects=True)
            ctype = r.headers.get("content-type", "").split(";")[0]
            if r.status_code == 200 and ctype.startswith("image/") and 200 < len(r.content) < 2_000_000:
                return {"url": url, "source": c["how"],
                        "data_uri": f"data:{ctype};base64," + base64.b64encode(r.content).decode()}
        except Exception:
            continue
    return {"url": None, "source": None, "data_uri": None}


_IDENTITY_SYSTEM = (
    "You extract facts about a business from its own website text. You never invent, infer, or "
    "paraphrase anything into existence. If a field is not explicitly stated on the pages, return null "
    "(or an empty list). Every value you return must include a 'quote' copied EXACTLY from the page text "
    "(max 40 words) and the 'source' URL it came from. Return only JSON."
)

_IDENTITY_PROMPT = """Business as submitted: {name} ({url}), location given: {location}.

Extract this JSON:
{{
  "business_name": {{"value": str|null, "quote": str, "source": str}},
  "category": "short plain category a customer would search, e.g. 'dog trainer', 'personal injury lawyer', 'HVAC contractor'",
  "city": "primary city served, from the site, else from the location given",
  "region": "state/region abbreviation if known",
  "tagline": {{"value": str|null, "quote": str, "source": str}},
  "mission": {{"value": str|null, "quote": str, "source": str}},
  "vision": {{"value": str|null, "quote": str, "source": str}},
  "values": [{{"value": str, "quote": str, "source": str}}],
  "leadership": [{{"name": str, "title": str|null, "quote": str, "source": str}}],
  "founded_year": {{"value": str|null, "quote": str, "source": str}},
  "services": [str],
  "service_areas": [str],
  "phone": str|null,
  "address": str|null,
  "voice": "2-5 words describing the writing voice, e.g. 'warm, family-run, plain-spoken'",
  "buyer_questions": ["{n} questions a real customer in {location} would type into ChatGPT when looking for this kind of business. Include the city. No brand names."]
}}

Mission/vision/values only count if the site presents them as such (e.g. 'Our mission is...').
Leadership only counts for named people with an owner/leadership/staff role on the site.

PAGES:
{pages}"""


def norm(s: str) -> str:
    s = (s or "").translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}))
    return re.sub(r"\s+", " ", s).strip().lower()


def _verify(item, corpus: str):
    """Drop any extracted statement whose quote is not actually on the site."""
    if not isinstance(item, dict):
        return None
    q = norm(item.get("quote") or "").strip(" .\"'")
    if not q or len(q) < 4:
        return None
    probe = q[:60]
    return item if probe in corpus else None


async def identity(name: str, url: str, location: str, pages: dict[str, str], n_questions: int) -> tuple[dict, str | None]:
    chunks, budget = [], 60000
    # Home first, then about/team-ish pages
    for u, text in sorted(pages.items(), key=lambda kv: (0 if urlparse(kv[0]).path in ("", "/") else 1,
                                                          0 if re.search(r"about|team|mission|leader", kv[0]) else 1)):
        t = text[:9000]
        if budget - len(t) < 0:
            break
        chunks.append(f"=== {u} ===\n{t}")
        budget -= len(t)
    data, err = await llm.claude_json(
        _IDENTITY_SYSTEM,
        _IDENTITY_PROMPT.format(name=name or "(not given)", url=url, location=location or "(not given)",
                                pages="\n\n".join(chunks), n=n_questions),
        max_tokens=4000,
    )
    if err or not isinstance(data, dict):
        return {}, err or "no data"
    corpus = norm(" ".join(pages.values()))
    for k in ("business_name", "tagline", "mission", "vision", "founded_year"):
        data[k] = _verify(data.get(k), corpus) if data.get(k) and data[k].get("value") else None
    data["values"] = [v for v in (data.get("values") or []) if _verify(v, corpus)]
    data["leadership"] = [p for p in (data.get("leadership") or [])
                          if p.get("name") and p["name"].lower() in corpus and _verify(p, corpus)]
    data["services"] = [s for s in (data.get("services") or []) if isinstance(s, str)][:12]
    data["service_areas"] = [s for s in (data.get("service_areas") or []) if isinstance(s, str)][:20]
    data["buyer_questions"] = [q for q in (data.get("buyer_questions") or []) if isinstance(q, str)][:n_questions]
    return data, None
