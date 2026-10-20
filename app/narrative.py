"""The written story on top of the numbers. Claude writes it; code checks it; a plain fallback ships if Claude fails."""
from __future__ import annotations

import json

from . import llm
from .model import FAIL, PARTIAL

SYSTEM = """You are the senior strategist at Immersive Agentics writing a prospect's AI Visibility & Authority audit.
Voice: direct, plain English, no buzzwords (never 'leverage', 'synergy', 'holistic'), treat the owner like a smart adult.

Hard rules:
- Use ONLY facts in the AUDIT DATA. Never invent numbers, competitors, quotes, or losses.
- Do not manufacture urgency. No 'window is closing', no fear tactics. Let the evidence carry it.
- If the score is strong, say so plainly and congratulate them; then point to what would extend the lead.
- Every finding must cite the check ids it comes from.
- Name competitors only if they appear in the data.
- Return ONLY JSON."""

PROMPT = """AUDIT DATA:
{data}

Write this JSON:
{{
  "headline": "max 14 words. The single most important truth about their AI visibility.",
  "executive_summary": "3 sentences max. Where they stand, the biggest gap or strength, what it means for customers finding them.",
  "findings": [
    {{"title": "max 8 words", "detail": "1-2 sentences in business terms, grounded in the evidence", "check_ids": ["..."], "tone": "gap"|"strength"}}
  ],
  "lost_opportunity": "2-3 sentences translating the AI Answers + Market evidence into customers they are or aren't reaching. If they are doing well, say what they're winning. Null if AI answers weren't measured.",
  "roadmap": [
    {{"priority": 1, "title": "...", "why": "1 sentence", "actions": ["2-3 concrete actions they could do themselves"], "check_ids": ["..."]}},
    {{"priority": 2, "title": "...", "why": "1 sentence", "actions": ["..."], "check_ids": ["..."]}},
    {{"priority": 3, "title": "Ongoing authority infrastructure", "why": "1 sentence on why the remaining gaps need a system, not a one-time fix", "actions": ["map the remaining gaps to the Immersive Authority System parts listed in sells fields"], "check_ids": ["..."]}}
  ],
  "email_intro": "2 sentences addressed to {first_name} for the delivery email. Mention the score and one specific finding. No hype."
}}
Give exactly 3 findings (mix strengths and gaps honestly by what the data shows) and exactly 3 roadmap priorities."""


def _compact(result: dict) -> dict:
    scored = result["scores"]
    secs = []
    for s in scored["sections"]:
        secs.append({"section": s["title"], "score": s["score"], "sells": s["sells"],
                     "checks": [{"id": c["id"], "label": c["label"], "status": c["status"],
                                 "earned": c["earned"], "points": c["points"], "evidence": c["evidence"]}
                                for c in s["checks"] if c["status"] != "info"]})
    ai = result.get("ai") or {}
    return {
        "business": result["business"],
        "overall_score": scored["overall_score"], "band": scored["overall_band"],
        "coverage_pct": scored["coverage_pct"],
        "sections": secs,
        "ai_top_named": ai.get("top_named"),
        "ai_engines": ai.get("engines"),
        "competitors": [{"name": c.get("name"), "maps_rank": c.get("maps_rank"), "reviews": c.get("review_count"),
                         "rating": c.get("rating"), "comparable_score": c.get("comparable_score")}
                        for c in (result.get("market") or {}).get("competitors", [])],
        "you_comparable_score": (result.get("market") or {}).get("you_comparable_score"),
    }


def fallback(result: dict) -> dict:
    """Plain, factual narrative built from the checks alone."""
    scored = result["scores"]
    allc = [c for s in scored["sections"] for c in s["checks"]]
    gaps = sorted([c for c in allc if c["status"] in (FAIL, PARTIAL) and c["points"]],
                  key=lambda c: -(c["points"] - c["earned"]))
    wins = [c for c in allc if c["status"] == "pass" and c["points"] >= 2]
    name = result["business"]["name"]
    findings = [{"title": c["label"], "detail": c["evidence"], "check_ids": [c["id"]], "tone": "gap"} for c in gaps[:2]]
    findings += [{"title": c["label"], "detail": c["evidence"], "check_ids": [c["id"]], "tone": "strength"} for c in wins[:1]]
    rm = [{"priority": i + 1, "title": c["label"], "why": c["evidence"], "actions": [c["fix"]] if c["fix"] else [],
           "check_ids": [c["id"]]} for i, c in enumerate(gaps[:2])]
    rm.append({"priority": 3, "title": "Ongoing authority infrastructure",
               "why": "The remaining gaps are ongoing work: content, reviews, listings and AI signals need to be maintained every month.",
               "actions": sorted({c["sells"] for c in gaps[2:8] if c["sells"]}), "check_ids": [c["id"] for c in gaps[2:8]]})
    score = scored["overall_score"]
    return {
        "headline": f"{name} scores {score}/100 for AI visibility and authority." if score is not None else f"AI visibility audit for {name}",
        "executive_summary": f"{name} scored {score}/100 ({scored['overall_band']}). "
                             + (f"The biggest gap is {gaps[0]['label'].lower()}." if gaps else "No major gaps found."),
        "findings": findings, "lost_opportunity": None, "roadmap": rm,
        "email_intro": f"Your AI Visibility & Authority score is {score}/100. The full breakdown is attached.",
        "generated_by": "fallback",
    }


async def write(result: dict) -> dict:
    data = _compact(result)
    first = (result["business"].get("contact_name") or "there").split()[0]
    out, err = await llm.claude_json(SYSTEM, PROMPT.format(data=json.dumps(data, default=str)[:60000], first_name=first),
                                     max_tokens=3000)
    if err or not isinstance(out, dict):
        fb = fallback(result)
        fb["error"] = err
        return fb
    valid_ids = {c["id"] for s in result["scores"]["sections"] for c in s["checks"]}
    for f in out.get("findings") or []:
        f["check_ids"] = [i for i in f.get("check_ids") or [] if i in valid_ids]
    for r in out.get("roadmap") or []:
        r["check_ids"] = [i for i in r.get("check_ids") or [] if i in valid_ids]
    if len(out.get("findings") or []) < 1 or len(out.get("roadmap") or []) < 3:
        fb = fallback(result)
        out = {**fb, **{k: v for k, v in out.items() if v}}
    out["generated_by"] = "claude"
    return out
