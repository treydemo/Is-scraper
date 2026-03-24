# IA Website Intelligence Engine

Scrapes any website + runs a full SEO/AEO/GEO audit via Claude AI.
Built for Immersive Agentics — Garland Chamber Workshop, March 2025.

---

## DEPLOY TO RAILWAY (Step by Step)

### Step 1 — Push to GitHub
1. Create a new GitHub repo called `ia-scraper`
2. Upload ALL files in this folder to that repo
3. Commit

### Step 2 — Deploy on Railway
1. Go to railway.app → New Project
2. Choose "Deploy from GitHub repo"
3. Select `ia-scraper`
4. Railway auto-detects the Dockerfile and builds it

### Step 3 — Set Environment Variable
1. In Railway dashboard → your project → Variables
2. Add: `ANTHROPIC_API_KEY` = your key from console.anthropic.com

### Step 4 — Get Your URL
1. Railway → Settings → Networking → Generate Domain
2. Copy your URL (looks like: ia-scraper-production.up.railway.app)

---

## HOW TO CALL IT (Make.com HTTP Module)

**Method:** POST  
**URL:** https://YOUR-RAILWAY-URL/audit  
**Body (JSON):**
```json
{
  "url": "{{url from Tally}}",
  "business_name": "{{business name from Tally}}",
  "contact_name": "{{contact name from Tally}}",
  "challenge": "{{challenge from Tally}}"
}
```

---

## TEST IT LOCALLY

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your Anthropic API key to .env
uvicorn main:app --reload
# Visit http://localhost:8000
```

Test with curl:
```bash
curl -X POST http://localhost:8000/audit \
  -H "Content-Type: application/json" \
  -d '{"url": "immersiveagentics.com", "business_name": "Immersive Agentics", "contact_name": "Trey", "challenge": "Getting more leads"}'
```
