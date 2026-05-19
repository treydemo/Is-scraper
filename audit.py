import os
import json
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = (
    "You are the Immersive Authority & Visibility Audit Engine. You analyze local businesses "
    "across 5 channels and produce honest, specific, actionable audit reports. "
    "Never be generic. Every finding must reference the specific business. "
    "Never invent data not provided to you. Return ONLY valid JSON, no markdown, no preamble."
)

USER_TEMPLATE = """\
Analyze this business audit data and return the JSON report.

BUSINESS:
- Name: {business_name}
- Contact: {contact_name}
- Location: {location}
- Stated Challenge: {challenge}

COMPUTED SCORES (use as reference, adjust ±10 based on qualitative factors):
- SEO Trust Score: {seo_score}/100
- AI Visibility Score: {ai_score}/100

AUDIT DATA:
{channel_json}

Return ONLY this exact JSON structure (no markdown, no preamble):

{{
  "executive_summary": "3 sentences. Specific to this business. What is their biggest problem and what does it cost them.",

  "brand_intelligence": {{
    "what_they_say": "1-sentence summary of their positioning based on title/h1/body text",
    "voice_tone": "professional/casual/authoritative/corporate/friendly/technical",
    "brand_color_assessment": "1-2 sentences — does the color scheme convey trust and authority for their industry?",
    "brand_gap": "biggest brand credibility gap found. Be specific. Not generic."
  }},

  "channel_scores": {{
    "website": {{
      "score": 0,
      "wins": ["specific win referencing actual scraped data"],
      "issues": ["specific issue referencing actual scraped data"],
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
    "social": {{
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
    }}
  }},

  "top_seo_gaps": [
    "Specific SEO gap 1 with its business impact",
    "Specific SEO gap 2 with its business impact",
    "Specific SEO gap 3 with its business impact"
  ],

  "top_visibility_gaps": [
    "Specific visibility gap 1",
    "Specific visibility gap 2",
    "Specific visibility gap 3"
  ],

  "quick_wins": [
    "Specific action completable in 1 week — reference actual gap",
    "Specific action completable in 1 week — reference actual gap",
    "Specific action completable in 1 week — reference actual gap"
  ],

  "recommended_next_steps": [
    "30-day priority action 1",
    "30-day priority action 2",
    "30-day priority action 3"
  ],

  "ia_pitch": "4 sentences. Sentence 1: name their single biggest specific gap by name. Sentence 2: name the competitor advantage they are losing to right now. Sentence 3: state exactly which Immersive Agentics service fixes it. Sentence 4: urgency — the window to own this position in their market is closing and competitors are moving fast."
}}
"""


async def run_audit(
    website_data: dict,
    pagespeed_data: dict,
    gbp_data: dict,
    lsa_data: dict,
    youtube_data: dict,
    social_data: dict,
    ai_citation_data: dict,
    schema_data: dict,
    competitors: list,
    business_name: str,
    contact_name: str,
    challenge: str,
    location: str,
    seo_score: int,
    ai_score: int,
) -> dict:
    # Trim body_text_sample to keep prompt manageable
    trimmed = dict(website_data)
    trimmed["body_text_sample"] = trimmed.get("body_text_sample", "")[:2000]

    channel_summary = {
        "website": {
            "scrape_status": trimmed.get("scrape_status"),
            "title": trimmed.get("title"),
            "meta_description": trimmed.get("meta_description"),
            "h1": trimmed.get("h1"),
            "h2s": trimmed.get("h2s", [])[:5],
            "is_https": trimmed.get("is_https"),
            "has_sitemap": trimmed.get("has_sitemap"),
            "has_robots": trimmed.get("has_robots"),
            "schema_types_found": trimmed.get("schema_types_found", []),
            "images_missing_alt": trimmed.get("images_missing_alt"),
            "internal_links_count": trimmed.get("internal_links_count"),
            "tagline": trimmed.get("tagline"),
            "mission": trimmed.get("mission"),
            "leadership": trimmed.get("leadership", []),
            "brand_colors": trimmed.get("brand_colors", {}),
            "body_excerpt": trimmed.get("body_text_sample", "")[:500],
        },
        "pagespeed": pagespeed_data,
        "gbp": gbp_data,
        "lsa": lsa_data,
        "youtube": youtube_data,
        "social": social_data,
        "ai_citation": ai_citation_data,
        "schema": schema_data,
        "competitors": competitors,
    }

    prompt = USER_TEMPLATE.format(
        business_name=business_name or "Unknown",
        contact_name=contact_name or "Business Owner",
        location=location or "Not specified",
        challenge=challenge or "Not specified",
        seo_score=seo_score,
        ai_score=ai_score,
        channel_json=json.dumps(channel_summary, indent=2),
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as e:
        return _empty_report(f"Claude API error: {str(e)}")

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        raw = "\n".join(inner).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return _empty_report("Claude returned malformed JSON")


def _empty_report(error: str) -> dict:
    empty_channel = {"score": 0, "wins": [], "issues": [error], "recommendations": []}
    return {
        "executive_summary": "",
        "brand_intelligence": {
            "what_they_say": "",
            "voice_tone": "unknown",
            "brand_color_assessment": "",
            "brand_gap": "",
        },
        "channel_scores": {
            "website": empty_channel,
            "gbp": {**empty_channel, "issues": []},
            "lsa": {**empty_channel, "issues": []},
            "social": {**empty_channel, "issues": []},
            "youtube": {**empty_channel, "issues": []},
        },
        "top_seo_gaps": [],
        "top_visibility_gaps": [],
        "quick_wins": [],
        "recommended_next_steps": [],
        "ia_pitch": "",
    }
