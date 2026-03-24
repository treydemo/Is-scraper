import os
import json
import httpx
from fastapi import HTTPException

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """You are the Website Intelligence Engine for Immersive Agentics, an AI marketing agency based in Dallas, TX. 

Your job is to analyze scraped website data and produce a compelling, specific, and genuinely useful audit report for small business owners.

CRITICAL RULES:
- Be specific. Use actual data from the scrape. Never be generic.
- Be direct and plain-spoken. No buzzwords. No "leverage" or "synergy."
- The "Didn't Know That" section must contain real surprises — things that make the owner say "whoa."
- Scores must be honest. A bad website gets a bad score. Don't sugarcoat.
- The tone is professional but warm — like a smart friend who happens to be an expert.
- Always end with a clear, specific next step CTA toward booking a call with Immersive Agentics.

SCORING GUIDE:
- 80-100: Strong foundation, minor gaps
- 60-79: Functional but missing key elements
- 40-59: Significant gaps hurting visibility
- Below 40: Critical issues — essentially invisible online

OUTPUT FORMAT: Return a single clean JSON object. No markdown. No preamble. Just the JSON."""

AUDIT_PROMPT = """Analyze the following website intelligence data and produce a full audit report.

SCRAPED DATA:
{scraped_json}

BUSINESS CONTEXT:
- Business Name: {business_name}
- Contact: {contact_name}
- Their Stated Challenge: {challenge}

Return ONLY a valid JSON object in this exact structure:

{{
  "brand_snapshot": {{
    "what_they_say": "1-2 sentences on how the business presents itself",
    "what_it_actually_communicates": "honest assessment of the actual message landing",
    "brand_gap": "specific gap between intent and reality",
    "voice_tone": "formal / casual / corporate / inconsistent / unclear",
    "nap_consistency": "consistent / inconsistent / incomplete — with specifics"
  }},
  "seo_score": 0,
  "seo_summary": "2-3 sentence plain english summary of SEO health",
  "seo_wins": ["win 1", "win 2"],
  "seo_gaps": ["gap 1", "gap 2", "gap 3"],
  "aeo_score": 0,
  "aeo_summary": "2-3 sentence plain english summary of AI search visibility",
  "aeo_wins": ["win 1"],
  "aeo_gaps": ["gap 1", "gap 2", "gap 3"],
  "geo_score": 0,
  "geo_summary": "2-3 sentence plain english summary of generative engine authority",
  "geo_wins": ["win 1"],
  "geo_gaps": ["gap 1", "gap 2", "gap 3"],
  "overall_score": 0,
  "wow_findings": [
    {{
      "headline": "short punchy headline for this finding",
      "detail": "1-2 sentences explaining what was found and why it matters"
    }},
    {{
      "headline": "short punchy headline for this finding",
      "detail": "1-2 sentences explaining what was found and why it matters"
    }},
    {{
      "headline": "short punchy headline for this finding",
      "detail": "1-2 sentences explaining what was found and why it matters"
    }}
  ],
  "top_priority": "The single most important thing they should fix first and why",
  "cta": "Personalized next step — mention their business name and challenge if available. Invite them to book a free strategy call with Immersive Agentics at immersiveagentics.com"
}}"""

async def run_audit(scraped_data: dict, business_name: str, contact_name: str, challenge: str) -> dict:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    prompt = AUDIT_PROMPT.format(
        scraped_json=json.dumps(scraped_data, indent=2),
        business_name=business_name or "Unknown",
        contact_name=contact_name or "Business Owner",
        challenge=challenge or "Not specified"
    )

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Claude API error: {str(e)}")

    result = response.json()
    raw_text = result["content"][0]["text"].strip()

    # Strip markdown fences if Claude adds them
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
    raw_text = raw_text.strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # Return raw text in a wrapper if JSON parse fails
        return {"raw_report": raw_text, "parse_error": True}
