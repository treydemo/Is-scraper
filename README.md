# IA Website Intelligence Engine — v5

Prospect audit for Immersive Agentics. Bob submits his website on Tally → this engine measures his AI visibility and
authority against what IA sells → Make emails him a branded score + PDF and copies us. Tracked in Jira **IA-80**.

## What it measures (100 points, deterministic)

| Section | Pts | How | Sells |
|---|---|---|---|
| AI Answers | 25 | Buyer questions asked live on ChatGPT / Gemini / Perplexity / Claude (web search on); share of voice; "what AI says about you" accuracy | Immersive Authority System |
| AI-Readable Website | 20 | Raw HTML (what GPTBot sees) vs rendered page; AI bot rules in robots.txt; LocalBusiness + rich schema; sitemap; llms.txt; PageSpeed; NAP | Authority Site |
| Answer Content | 15 | FAQs, question headings, service/area pages, proof, freshness, about/leadership, mission/values | Knowledge Hub |
| Google Footprint | 15 | Places API (New): match, rating, reviews, recency, hours, photos, category | Distribution Engine (GBP) |
| Local Market Position | 15 | Maps rank for "<category> in <city>", reviews/rating vs top 3, AI share vs AI's favorite | Authority Engine |
| Distribution Reach | 10 | Social profiles, YouTube activity, video on site | Distribution Engine |

Rules: code scores, Claude only writes the narrative (and a fallback ships if Claude fails). Anything we can't measure
(missing key, bot wall) is **not measured** and excluded — never scored as zero. Bot-protection challenge pages are
detected and never reported as "your site is broken". Extracted mission/vision/values/leadership must quote the site
verbatim or they're dropped.

Also captured: logo, brand colors (computed), fonts, tagline, mission, vision, values, leadership, services, service areas.

## API

```
POST /jobs                 X-API-Key   → {job_id, report_url, pdf_url}   (returns immediately)
GET  /jobs/{id}            X-API-Key   → status/step/summary
POST /jobs/{id}/resend     X-API-Key   → re-fire callback
GET  /r/{id}?t=TOKEN                   → hosted HTML report
GET  /r/{id}.pdf?t=TOKEN               → PDF
GET  /r/{id}.json?t=TOKEN              → full audit data
GET  /                                 → health + which engines are configured
```

`POST /jobs` body (flat), or a raw Tally webhook payload with `callback_url` added:
```json
{"url": "lonestark9.com", "business_name": "...", "contact_name": "...", "email": "...", "phone": "...",
 "location": "Plano, Texas", "challenge": "...", "facebook_url": "", "instagram_url": "", "linkedin_url": "",
 "callback_url": "https://hook.us2.make.com/...", "force": false, "passthrough": {"any": "data"}}
```
Same domain within `DEDUPE_DAYS` re-sends the cached audit unless `force: true`.

When the audit finishes the engine POSTs to `callback_url`:
`{job_id, status: "done"|"failed", summary{...scores, brand...}, email_subject, email_html, pdf_url, report_url, pdf_filename, request{...}, passthrough}`.
Make downloads `pdf_url`, sends `email_html` with the PDF attached, BCCs IA, logs to Airtable.

## Deploy (Railway)

Dockerfile build. Set the env vars in `env.example`. Add a volume at `/data`. One worker (jobs run in-process,
`MAX_CONCURRENT_JOBS` at a time). Typical audit: 3–6 minutes.

## Test

```
pip install -r requirements.txt pytest
pytest -q        # offline end-to-end: fake site, fake Google, fake AI engines → real scoring + real PDF in tests/out/
```
