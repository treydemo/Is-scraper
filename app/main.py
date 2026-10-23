"""IA Website Intelligence Engine v5 — HTTP API.

POST /jobs            start an audit (X-API-Key). Returns immediately with job_id + links.
GET  /jobs/{id}       job status/summary (X-API-Key)
GET  /r/{id}?t=       hosted HTML report (token link, safe to email)
GET  /r/{id}.pdf?t=   PDF
GET  /r/{id}.json?t=  full audit data
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import config, jobs, llm
from .fetch import normalize_url
from .report import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs full request URLs at INFO, and Google APIs take the key as a ?key= query param,
# which put GOOGLE_API_KEY in plain text in the Railway logs. Keep httpx/httpcore at WARNING.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
app = FastAPI(title="IA Website Intelligence Engine", version=config.VERSION)


@app.on_event("startup")
async def _startup():
    jobs.init()
    # Re-run anything interrupted by a redeploy
    with jobs._conn() as c:
        stale = [r["id"] for r in c.execute("select id from jobs where status in ('queued','running')")]
    for jid in stale:
        asyncio.create_task(jobs.run(jid))


def _auth(key: str | None):
    if not config.AUDIT_API_KEY:
        raise HTTPException(503, "AUDIT_API_KEY not set on the server")
    if not key or not hmac.compare_digest(key, config.AUDIT_API_KEY):
        raise HTTPException(401, "bad api key")


def _flatten(body: dict) -> dict:
    """Accepts either our flat schema or a raw Tally webhook payload (data.fields[])."""
    if "data" in body and isinstance(body["data"], dict) and "fields" in body["data"]:
        out = {"source": "tally", "submission_id": body["data"].get("submissionId")}
        for f in body["data"]["fields"]:
            label = (f.get("label") or "").lower()
            val = f.get("value")
            if isinstance(val, list):
                opts = {o["id"]: o["text"] for o in f.get("options") or []}
                val = ", ".join(opts.get(v, str(v)) for v in val)
            if val in (None, "") or isinstance(val, bool):
                continue
            sval = str(val).lower()
            social = next((s for s in ("facebook", "instagram", "linkedin", "youtube") if s in label or s + ".com" in sval), None)
            if social:
                key = f"{social}_url"
            elif f.get("type") == "INPUT_URL" or "url" in label or "website" in label:
                key = "url"
            elif f.get("type") == "INPUT_EMAIL" or "email" in label:
                key = "email"
            elif f.get("type") == "INPUT_PHONE_NUMBER" or "phone" in label:
                key = "phone"
            elif "business name" in label or "company" in label:
                key = "business_name"
            elif "first" in label:
                key = "first_name"
            elif "last" in label:
                key = "last_name"
            elif "location" in label or "city" in label:
                key = "location"
            elif "challenge" in label:
                key = "challenge"
            elif f.get("type") == "INPUT_TEXT" and re.match(r"^(https?://)?(www\.)?[\w-]+(\.[\w-]+)+(/\S*)?$", sval.strip()):
                key = "url"     # unlabeled field that holds a website
            elif f.get("type") == "CHECKBOXES" and val:
                key = "challenge"
            else:
                continue
            out.setdefault(key, val)
        out["contact_name"] = " ".join(x for x in (out.pop("first_name", ""), out.pop("last_name", "")) if x)
        for k in ("callback_url", "force", "passthrough"):
            if k in body:
                out[k] = body[k]
        return out
    return body


@app.get("/")
async def health():
    return {"service": "IA Website Intelligence Engine", "version": config.VERSION,
            "ai_engines": llm.configured_engines(), "google_places": bool(config.GOOGLE_API_KEY),
            "auth_configured": bool(config.AUDIT_API_KEY)}


def _is_allowed_tally(body: dict) -> bool:
    d = body.get("data") if isinstance(body, dict) else None
    return (isinstance(d, dict) and body.get("eventType") == "FORM_RESPONSE"
            and d.get("formId") in config.TALLY_FORM_IDS and isinstance(d.get("fields"), list))


@app.get("/diag")
async def diag():
    """One cheap live call per dependency. Error text (no secrets) says which API/project needs fixing."""
    import httpx
    from .checks import google

    async def places():
        async with httpx.AsyncClient() as c:
            res, err = await google.places_search(c, "coffee shop Dallas TX", max_results=1)
        return "ok" if res else (err or "no results")

    async def youtube():
        if not config.GOOGLE_API_KEY:
            return "not configured"
        async with httpx.AsyncClient() as c:
            r = await c.get("https://www.googleapis.com/youtube/v3/channels", timeout=15,
                            params={"part": "id", "forHandle": "@Google", "key": config.GOOGLE_API_KEY})
        return "ok" if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:300]}"

    async def pagespeed():
        async with httpx.AsyncClient() as c:
            out = await google.pagespeed(c, "https://example.com")
        return "ok" if out.get("mobile_score") is not None else (out.get("error") or "no score")

    async def claude():
        data, err = await llm.claude_json("Return only JSON.", 'Return {"ok": true}', model=config.CLAUDE_FAST_MODEL,
                                          max_tokens=50)
        return "ok" if data else err

    async def engine(name):
        text, err = await llm.ENGINES[name]("Reply with the single word OK.", {"city": "Dallas", "region": "TX"})
        return "ok" if text else err

    names = ["places", "youtube", "pagespeed", "claude"] + [f"engine:{e}" for e in llm.configured_engines()]
    coros = [places(), youtube(), pagespeed(), claude()] + [engine(e) for e in llm.configured_engines()]
    results = await asyncio.gather(*coros, return_exceptions=True)
    return {"version": config.VERSION,
            "checks": {n: (r if isinstance(r, str) else f"{type(r).__name__}: {r}")[:400] for n, r in zip(names, results)}}


@app.post("/jobs")
async def create_job(request: Request, background: BackgroundTasks, x_api_key: str | None = Header(None)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body required")
    if x_api_key or not _is_allowed_tally(body):
        _auth(x_api_key)
    elif jobs.count_since(60) >= config.PUBLIC_HOURLY_CAP:
        raise HTTPException(429, "audit capacity reached, try again later")
    req = _flatten(body)
    if not normalize_url(req.get("url", "")) or "." not in req.get("url", ""):
        raise HTTPException(422, "url is required")
    job, cached = jobs.create(req)
    background.add_task(jobs.run, job["id"])
    return {"job_id": job["id"], "status": job["status"], "cached": cached, "domain": job["domain"],
            **jobs.links(job["id"], job["token"])}


@app.get("/jobs/{job_id}")
async def job_status(job_id: str, x_api_key: str | None = Header(None)):
    _auth(x_api_key)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return {k: job[k] for k in ("id", "domain", "status", "step", "created_at", "finished_at", "summary",
                                "error", "cached_from", "callback_status")} | jobs.links(job["id"], job["token"])


@app.post("/jobs/{job_id}/resend")
async def resend(job_id: str, x_api_key: str | None = Header(None)):
    """Re-fire the callback (e.g. after fixing the Make scenario)."""
    _auth(x_api_key)
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404, "no finished job")
    jpath, _, _ = jobs.result_paths(job)
    result = json.loads(jpath.read_text())
    status = await jobs._callback(job, jobs.payload_for(job, "done", result))
    jobs._update(job_id, callback_status=status)
    return {"callback_status": status}


def _job_for_token(job_id: str, t: str | None) -> dict:
    job = jobs.get(job_id)
    if not job or not t or not hmac.compare_digest(t, job["token"]):
        raise HTTPException(404, "not found")
    if job["status"] != "done":
        raise HTTPException(409, f"audit is {job['status']}: {job['step']}")
    return job


@app.get("/r/{job_id}.pdf")
async def report_pdf(job_id: str, t: str | None = None):
    job = _job_for_token(job_id, t)
    jpath, ppath, _ = jobs.result_paths(job)
    name = json.loads(jpath.read_text())["business"]["name"] if jpath.exists() else job["domain"]
    return FileResponse(ppath, media_type="application/pdf",
                        filename=f"AI-Visibility-Audit-{name}.pdf".replace(" ", "-").replace("/", "-"))


@app.get("/r/{job_id}.json")
async def report_json(job_id: str, t: str | None = None):
    job = _job_for_token(job_id, t)
    jpath, _, _ = jobs.result_paths(job)
    return JSONResponse(json.loads(jpath.read_text()))


@app.get("/r/{job_id}", response_class=HTMLResponse)
async def report_html(job_id: str, t: str | None = None):
    job = _job_for_token(job_id, t)
    jpath, _, _ = jobs.result_paths(job)
    return HTMLResponse(render.html(json.loads(jpath.read_text())))
