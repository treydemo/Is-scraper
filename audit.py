import os
import json
import httpx
from fastapi import HTTPException

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """You are the Immersive Authority & Visibility Audit Engine for Immersive Agentics, an AI marketing agency based in Dallas, TX.

You analyze businesses across 5 channels and return two master scores plus detailed channel breakdowns.

AUTHORITY = Are they positioned as a trusted, credible expert in their market?
VISIBILITY = Can customers find them everywhere they search?

CRITICAL RULES:
- Be specific. Use actual data from the channel results. Never be generic.
- Be direct and plain-spoken. No buzzwords. No "leverage" or "synergy."
- Scores must be honest. A weak channel gets a weak score. Do not sugarcoat.
- The ia_pitch must feel tailored — reference their actual gaps, not a generic pitch.
- The tone is professional but warm — like a smart friend who happens to be a marketing expert.

SCORING GUIDE:
- 80-100: Strong foundation, minor gaps
- 60-79: Functional but missing key elements
- 40-59: Significant gaps hurting authority/visibility
- Below 40: Critical issues — essentially invisible or uncredible online

AUTHORITY is weighted by: website quality, schema/FAQ presence, GBP completeness, YouTube depth, brand consistency
VISIBILITY is weighted by: GBP rating/reviews, LSA/local pack presence, social reach, YouTube subscribers, site indexability

OUTPUT FORMAT: Return a single clean JSON object. No markdown. No preamble. Just the JSON."""

AUDIT_PROMPT = """Analyze the following 5-channel data and produce a full Immersive Authority & Visibility Audit.

CHANNEL DATA:
{channel_json}

BUSINESS CONTEXT:
- Business Name: {business_name}
- Contact: {contact_name}
- Location: {location}
- Their Stated Challenge: {challenge}

Return ONLY a valid JSON object in this exact structure:

{{
  "authority_score": 0,
  "visibility_score": 0,
  "authority_grade": "A",
  "visibility_grade": "B",
  "executive_summary": "2-3 sentence plain English summary of their overall authority and visibility position",
  "channel_scores": {{
    "website": {{
      "score": 0,
      "wins": ["specific win from data"],
      "issues": ["specific issue from data"],
      "recommendations": ["specific actionable recommendation"]
    }},
    "gbp": {{
      "score": 0,
      "wins": [],
      "issues": [],
      "recommendations": []
    }},
    "lsa": {{
      "score": 0,
      "wins": [],
      "issues": [],
      "recommendations": []
    }},
    "youtube": {{
      "score": 0,
      "wins": [],
      "issues": [],
      "recommendations": []
    }},
    "socials": {{
      "score": 0,
      "wins": [],
      "issues": [],
      "recommendations": []
    }}
  }},
  "top_authority_gaps": ["gap1", "gap2", "gap3"],
  "top_visibility_gaps": ["gap1", "gap2", "gap3"],
  "quick_wins": ["specific action that can be done this week", "action2", "action3"],
  "recommended_next_steps": ["30-day priority step", "60-day step", "90-day step"],
  "brand_intelligence": {{
    "tagline": "their main H1 or hero text",
    "voice_tone": "formal / casual / corporate / conversational / inconsistent",
    "what_they_say": "1-2 sentences on how the business presents itself",
    "what_it_actually_communicates": "honest assessment of the message that actually lands",
    "brand_gap": "specific gap between stated intent and actual perception",
    "nap_consistency": "consistent / inconsistent / incomplete — with specifics",
    "brand_color_assessment": "1 sentence on whether their color palette feels on-brand for their industry"
  }},
  "ia_pitch": "1-2 sentence pitch for how Immersive Agentics specifically fixes the biggest gaps found — reference the actual gaps by name"
}}

GRADING SCALE:
- A: 80-100
- B: 65-79
- C: 50-64
- D: 35-49
- F: 0-34

When setting channel_scores, use the raw scores provided in the channel data as your starting point, then adjust ±10 based on qualitative factors you observe. Do not invent data not present in the channel results."""


async def run_audit(
    channel_data: dict,
    business_name: str,
    contact_name: str,
    challenge: str,
    location: str = "",
) -> dict:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Trim body_text_sample to keep prompt size manageable
    trimmed = json.loads(json.dumps(channel_data))
    if "website" in trimmed and "body_text_sample" in trimmed["website"]:
        trimmed["website"]["body_text_sample"] = trimmed["website"]["body_text_sample"][:3000]

    prompt = (
        AUDIT_PROMPT
        .replace("{channel_json}", json.dumps(trimmed, indent=2))
        .replace("{business_name}", business_name or "Unknown")
        .replace("{contact_name}", contact_name or "Business Owner")
        .replace("{location}", location or "Not specified")
        .replace("{challenge}", challenge or "Not specified")
    )

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=90) as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Claude API error: {str(e)}")

    raw_text = response.json()["content"][0]["text"].strip()

    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    raw_text = raw_text.strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        return {"raw_report": raw_text, "parse_error": True}
