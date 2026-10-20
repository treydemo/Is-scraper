"""Offline end-to-end: fake site, fake Google, fake AI engines -> real scoring, real narrative plumbing, real PDF."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("DATA_DIR", str(Path(__file__).parent / ".data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest

from app import audit, config, llm, narrative
from app.checks import ai_answers, brand
from app.fetch import RenderedPage, is_bot_wall
from app.report import render
from tests.fixtures import site as F

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


class FakeBrowser:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def render(self, url, collect_brand=False, timeout_ms=0):
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/") or "/"
        html = {"/": F.HOME, "/about": F.ABOUT}.get(path, F.ABOUT)
        p = RenderedPage(url=url, html=html, status=200)
        if collect_brand:
            p.brand = {"colors": {"#C8102E": {"weight": 400, "roles": {"button-bg": 300, "header-bg": 100}},
                                  "#0B2545": {"weight": 900, "roles": {"header-bg": 900}},
                                  "#FFFFFF": {"weight": 2000, "roles": {"body-bg": 2000}},
                                  "#222222": {"weight": 80, "roles": {"body-text": 80}}},
                       "fonts": {"heading": {"Montserrat": 9}, "body": {"Open Sans": 20}},
                       "fontLinks": ["https://fonts.googleapis.com/css2?family=Montserrat:wght@700&family=Open+Sans"],
                       "logo": [{"src": F.BASE + "/logo.png", "how": "header-img"}]}
            p.text = F.RENDERED_TEXT
        else:
            from app.fetch import visible_text
            p.text = visible_text(html)
        return p


async def fake_claude_json(system, prompt, model=None, max_tokens=0):
    if "extract facts about a business" in system:
        return {
            "business_name": {"value": "Lone Star K9 Academy", "quote": "Lone Star K9 Academy", "source": F.BASE},
            "category": "dog trainer", "city": "Plano", "region": "TX",
            "tagline": {"value": "Dog training that sticks", "quote": "Dog training that sticks.", "source": F.BASE},
            "mission": {"value": "Give every Plano family a dog they can trust anywhere.",
                        "quote": "Our mission is to give every Plano family a dog they can trust anywhere.", "source": F.BASE + "/about"},
            "vision": {"value": "Be the #1 trainer in Texas", "quote": "We will be the number one trainer in Texas", "source": F.BASE},
            "values": [{"value": "Consistency", "quote": "made up quote not on page", "source": F.BASE}],
            "leadership": [{"name": "Jake Morrison", "title": "Owner & Head Trainer",
                            "quote": "Jake Morrison, Owner & Head Trainer", "source": F.BASE + "/about"},
                           {"name": "Ghost Person", "title": "CEO", "quote": "Ghost Person, CEO", "source": F.BASE}],
            "founded_year": {"value": None}, "services": ["Obedience training", "Puppy training", "Behavior"],
            "service_areas": ["Plano", "Frisco"], "phone": "(972) 555-0142", "address": "100 Main St, Plano TX",
            "voice": "confident, plain-spoken",
            "buyer_questions": ["Who is the best dog trainer in Plano TX?", "Best puppy training near Plano?",
                                "Dog trainer for aggressive dogs in Plano"],
        }, None
    if "recommend or name" in system:
        ids = [int(x) for x in re.findall(r"### id (\d+)", prompt)]
        blocks = re.split(r"### id \d+", prompt)[1:]
        answers = []
        for i, blk in zip(ids, blocks):
            names = [n for n in ["Sit Means Sit Dog Training Plano", "Lone Star K9 Academy", "Bark Busters Plano"] if n in blk.split("Answer:")[1]]
            answers.append({"id": i, "businesses": names, "target_named": "Lone Star K9 Academy" in names,
                            "target_position": names.index("Lone Star K9 Academy") + 1 if "Lone Star K9 Academy" in names else None})
        return {"answers": answers}, None
    if "compare what an AI assistant said" in system:
        return {"verdicts": [{"engine": "ChatGPT", "knows_business": True, "accuracy": "accurate", "errors": [], "summary": "Correct."},
                             {"engine": "Gemini", "knows_business": True, "accuracy": "partly_wrong",
                              "errors": ["Says Dallas, site says Plano", "Claims grooming"], "summary": "Wrong city and services."},
                             {"engine": "Claude", "knows_business": True, "accuracy": "accurate", "errors": [], "summary": "Correct."},
                             {"engine": "Perplexity", "knows_business": True, "accuracy": "accurate", "errors": [], "summary": "Correct."}]}, None
    if "senior strategist" in system:
        data = json.loads(prompt.split("AUDIT DATA:\n", 1)[1].split("\n\nWrite this JSON", 1)[0])
        return {"headline": f"AI sends Plano dog owners to competitors — {data['overall_score']}/100",
                "executive_summary": "Solid reviews, but AI rarely names you.",
                "findings": [{"title": "Blocked from Perplexity", "detail": "robots.txt blocks it.", "check_ids": ["ai_bot_access", "bogus_id"], "tone": "gap"},
                             {"title": "Rarely recommended", "detail": "Named in few answers.", "check_ids": ["ai_sov"], "tone": "gap"},
                             {"title": "Strong rating", "detail": "4.8 stars.", "check_ids": ["gbp_rating"], "tone": "strength"}],
                "lost_opportunity": "Most AI answers sent buyers to Sit Means Sit.",
                "roadmap": [{"priority": 1, "title": "Unblock AI crawlers", "why": "x", "actions": ["Edit robots.txt"], "check_ids": ["ai_bot_access"]},
                            {"priority": 2, "title": "Server-render content", "why": "x", "actions": ["SSR"], "check_ids": ["js_dependency"]},
                            {"priority": 3, "title": "Ongoing authority infrastructure", "why": "x", "actions": ["Knowledge Hub"], "check_ids": ["faq"]}],
                "email_intro": "Hi Jake, you scored X."}, None
    return None, "unexpected prompt"


def _fake_engine(name):
    async def ask(q, loc):
        return F.ai_answer(name, q), None
    return ask


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(audit, "Browser", FakeBrowser)
    monkeypatch.setattr(llm, "claude_json", fake_claude_json)
    monkeypatch.setattr(llm, "ENGINES", {n: _fake_engine(n) for n in ["ChatGPT", "Gemini", "Perplexity", "Claude"]})
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "PERPLEXITY_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setattr(config, k, "test")


def run_audit():
    req = {"url": "lonestark9.com", "business_name": "Lone Star K9 Academy", "contact_name": "Jake Morrison",
           "email": "jake@lonestark9.com", "phone": "972-555-0142", "location": "Plano, Texas"}
    return asyncio.run(audit.run(req, transport=httpx.MockTransport(F.handler)))


def test_end_to_end_scores_and_report():
    r = run_audit()
    (OUT / "result.json").write_text(json.dumps(r, indent=2, default=str))
    s = r["scores"]
    secs = {x["key"]: x for x in s["sections"]}
    checks = {c["id"]: c for x in s["sections"] for c in x["checks"]}

    # Every section measured, deterministic overall
    assert s["coverage_pct"] == 100
    assert all(secs[k]["score"] is not None for k in secs)
    assert 0 < s["overall_score"] < 100

    # Crawler realities
    assert checks["ai_bot_access"]["status"] == "partial"           # Perplexity blocked
    assert "Perplexity" in checks["ai_bot_access"]["evidence"]
    assert r["site"]["robots"]["training"]["GPTBot"] is False
    assert checks["js_dependency"]["status"] in ("fail", "partial")  # raw home is a JS shell
    assert checks["schema_local"]["status"] == "partial"             # LocalBusiness missing hours/geo etc.
    assert checks["nap"]["status"] == "pass"
    assert checks["sitemap"]["status"] == "pass"
    assert checks["speed"]["status"] in ("partial", "fail")

    # Google + market
    assert r["google"]["matched_by"] == "website"
    assert r["market"]["rank"] == 3
    assert [c["name"] for c in r["market"]["competitors"]][:2] == ["Sit Means Sit Dog Training Plano", "Bark Busters Plano"]
    assert checks["map_rank"]["earned"] == 5

    # AI answers: only ChatGPT names us, on the puppy question
    ai = r["ai"]
    assert ai["answers_total"] == 12 and ai["answers_named"] == 1
    assert ai["by_engine"]["ChatGPT"]["named"] == 1 and ai["by_engine"]["Gemini"]["named"] == 0
    assert ai["top_named"][0]["name"] == "Sit Means Sit Dog Training Plano"
    assert checks["ai_brand"]["status"] == "partial"
    assert "Sit Means Sit" in checks["ai_vs"]["evidence"]

    # Brand capture + anti-hallucination
    b = r["brand"]
    assert b["palette"]["primary"] in ("#C8102E", "#0B2545")
    assert b["fonts"]["heading"] == "Montserrat" and "Open Sans" in b["fonts"]["web_font_families"]
    assert b["logo"]["data_uri"].startswith("data:image/png")
    assert b["mission"] and "trust anywhere" in b["mission"]["value"]
    assert b["vision"] is None                        # quote not on site -> dropped
    assert b["values"] == []                          # fabricated quote -> dropped
    assert [p["name"] for p in b["leadership"]] == ["Jake Morrison"]

    # Narrative: invalid check ids stripped
    assert "bogus_id" not in r["narrative"]["findings"][0]["check_ids"]

    html = render.html(r)
    assert "Lone Star K9 Academy" in html and "Sit Means Sit" in html
    pdf = render.pdf(r)
    assert pdf[:4] == b"%PDF" and len(pdf) > 20000
    (OUT / "report.pdf").write_bytes(pdf)
    (OUT / "report.html").write_text(html)
    (OUT / "email.html").write_text(render.email_html(r, "https://example.com/r/x"))


def test_deterministic():
    a, b = run_audit(), run_audit()
    assert a["scores"] == b["scores"]


def test_blocked_site_is_not_scored_as_broken(monkeypatch):
    wall = "<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"

    def walled(request):
        if str(request.url).startswith(F.BASE) or "lonestark9.com" in str(request.url):
            return httpx.Response(403, text=wall, headers={"content-type": "text/html"})
        return F.handler(request)

    class WalledBrowser(FakeBrowser):
        async def render(self, url, collect_brand=False, timeout_ms=0):
            return RenderedPage(url=url, html=wall, status=403, walled=True)

    monkeypatch.setattr(audit, "Browser", WalledBrowser)
    req = {"url": "lonestark9.com", "business_name": "Lone Star K9 Academy", "location": "Plano, TX", "phone": "972-555-0142"}
    r = asyncio.run(audit.run(req, transport=httpx.MockTransport(walled)))
    secs = {x["key"]: x for x in r["scores"]["sections"]}
    assert secs["ai_readable"]["score"] is None and secs["content"]["score"] is None
    assert "bot protection" in r["scan"]["note"]
    assert r["scores"]["overall_score"] is not None   # Google + AI still scored
    assert r["scores"]["coverage_pct"] < 100
    assert render.pdf(r)[:4] == b"%PDF"


def test_bot_wall_detection():
    assert is_bot_wall(503, "<title>Just a moment...</title>")
    assert is_bot_wall(200, "<html>Checking your browser before accessing</html>")
    assert not is_bot_wall(200, "<html><body>" + "real content " * 100 + "</body></html>")


def test_mentions():
    assert ai_answers._mentions("Try Lone Star K9 Academy in Plano", "Lone Star K9 Academy", "")
    assert ai_answers._mentions("check lonestark9.com", "Lone Star K9 Academy", "lonestark9.com")
    assert not ai_answers._mentions("Try Lone Wolf Training", "Lone Star K9 Academy", "lonestark9.com")


def test_narrative_fallback_when_claude_fails(monkeypatch):
    r = run_audit()

    async def boom(*a, **k):
        return None, "HTTP 500"
    monkeypatch.setattr(llm, "claude_json", boom)
    n = asyncio.run(narrative.write(r))
    assert n["generated_by"] == "fallback" and len(n["roadmap"]) == 3 and n["headline"]


def test_tally_flatten():
    from app.main import _flatten
    body = {"eventType": "FORM_RESPONSE", "data": {"submissionId": "s1", "fields": [
        {"label": "copy and past you exact business URL", "type": "INPUT_URL", "value": "https://x.com"},
        {"label": "Business Name", "type": "INPUT_TEXT", "value": "X Co"},
        {"label": "First Name", "type": "INPUT_TEXT", "value": "Bob"},
        {"label": "Last Name", "type": "INPUT_TEXT", "value": "Smith"},
        {"label": "Email", "type": "INPUT_EMAIL", "value": "bob@x.com"},
        {"label": "Business Phone", "type": "INPUT_PHONE_NUMBER", "value": "+19725550000"},
        {"label": "Business Location", "type": "INPUT_TEXT", "value": "Dallas, Texas"},
        {"label": "Facebook Business Page URL", "type": "INPUT_URL", "value": "https://facebook.com/x"},
        {"label": "YouTube Channel (optional)", "type": "INPUT_TEXT", "value": "https://youtube.com/@x"},
        {"label": "What is you biggest marketing challenge? (Not showing up on google)", "type": "CHECKBOXES", "value": True},
        {"label": "What is you biggest marketing challenge?", "type": "MULTIPLE_CHOICE", "value": ["o1"],
         "options": [{"id": "o1", "text": "Not showing up on google"}]}]},
        "callback_url": "https://hook.make.com/abc"}
    out = _flatten(body)
    assert out["url"] == "https://x.com" and out["facebook_url"] == "https://facebook.com/x"
    assert out["youtube_url"] == "https://youtube.com/@x"
    assert out["contact_name"] == "Bob Smith" and out["email"] == "bob@x.com"
    assert out["challenge"] == "Not showing up on google" and out["callback_url"].startswith("https://hook")


def test_public_tally_intake_rules():
    from app.main import _is_allowed_tally
    ok = {"eventType": "FORM_RESPONSE", "data": {"formId": "lbY20B", "fields": []}}
    assert _is_allowed_tally(ok)
    assert not _is_allowed_tally({"eventType": "FORM_RESPONSE", "data": {"formId": "OTHER", "fields": []}})
    assert not _is_allowed_tally({"url": "x.com"})
