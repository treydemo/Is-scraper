"""Job store (SQLite on the data volume) + background runner + callback delivery."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import time
import traceback
from datetime import datetime, timedelta, timezone

import httpx

from . import audit, config
from .fetch import domain_of, normalize_url
from .report import render

log = logging.getLogger("jobs")
DB = config.DATA_DIR / "jobs.db"
FILES = config.DATA_DIR / "reports"
FILES.mkdir(parents=True, exist_ok=True)
_sem = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)


def _conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute("""create table if not exists jobs (
            id text primary key, token text, domain text, status text, step text, created_at text,
            finished_at text, request text, summary text, error text, cached_from text, callback_status text)""")
        c.execute("create index if not exists jobs_domain on jobs(domain, status, created_at)")


def _now():
    return datetime.now(timezone.utc).isoformat()


def get(job_id: str) -> dict | None:
    with _conn() as c:
        r = c.execute("select * from jobs where id=?", (job_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for k in ("request", "summary"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def _update(job_id: str, **kw):
    if "summary" in kw and kw["summary"] is not None:
        kw["summary"] = json.dumps(kw["summary"])
    sets = ", ".join(f"{k}=?" for k in kw)
    with _conn() as c:
        c.execute(f"update jobs set {sets} where id=?", (*kw.values(), job_id))


def recent_for_domain(domain: str) -> dict | None:
    since = (datetime.now(timezone.utc) - timedelta(days=config.DEDUPE_DAYS)).isoformat()
    with _conn() as c:
        r = c.execute("select id from jobs where domain=? and status='done' and cached_from is null and created_at>? "
                      "order by created_at desc limit 1", (domain, since)).fetchone()
    return get(r["id"]) if r else None


def links(job_id: str, token: str) -> dict:
    base = config.PUBLIC_BASE_URL
    return {"report_url": f"{base}/r/{job_id}?t={token}", "pdf_url": f"{base}/r/{job_id}.pdf?t={token}",
            "json_url": f"{base}/r/{job_id}.json?t={token}"}


def create(req: dict) -> tuple[dict, bool]:
    url = normalize_url(req["url"])
    req = {**req, "url": url}
    domain = domain_of(url)
    cached = None if req.get("force") else recent_for_domain(domain)
    jid = "aud_" + secrets.token_hex(6)
    token = secrets.token_urlsafe(18)
    with _conn() as c:
        c.execute("insert into jobs (id, token, domain, status, step, created_at, request, cached_from) values (?,?,?,?,?,?,?,?)",
                  (jid, token, domain, "queued", "Queued", _now(), json.dumps(req), cached["id"] if cached else None))
    return get(jid), bool(cached)


def _paths(job_id: str):
    return FILES / f"{job_id}.json", FILES / f"{job_id}.pdf", FILES / f"{job_id}.email.html"


def result_paths(job: dict):
    """A cached job serves the original audit's files."""
    return _paths(job["cached_from"] or job["id"])


def summary_of(result: dict) -> dict:
    s = result["scores"]
    b = result["business"]
    ai = result.get("ai") or {}
    g = (result.get("google") or {}).get("profile") or {}
    m = result.get("market") or {}
    br = result.get("brand") or {}
    return {
        "business_name": b["name"], "domain": b["domain"], "city": b["city"], "category": b["category"],
        "overall_score": s["overall_score"], "overall_grade": s["overall_grade"], "overall_band": s["overall_band"],
        "coverage_pct": s["coverage_pct"],
        "section_scores": {x["key"]: x["score"] for x in s["sections"]},
        "ai_share_of_voice": ai.get("share_of_voice"), "ai_engines": ai.get("engines"),
        "top_ai_competitor": next((t["name"] for t in ai.get("top_named") or [] if not t.get("is_target")), None),
        "maps_rank": m.get("rank"), "gbp_rating": g.get("rating"), "gbp_reviews": g.get("review_count"),
        "competitors": [c.get("name") for c in m.get("competitors") or []],
        "headline": result["narrative"].get("headline"),
        "brand": {"primary_color": (br.get("palette") or {}).get("primary"),
                  "accent_color": (br.get("palette") or {}).get("accent"),
                  "heading_font": (br.get("fonts") or {}).get("heading"), "body_font": (br.get("fonts") or {}).get("body"),
                  "logo_url": (br.get("logo") or {}).get("url"),
                  "mission": (br.get("mission") or {}).get("value"), "vision": (br.get("vision") or {}).get("value"),
                  "values": [v["value"] for v in br.get("values") or []],
                  "leadership": [f"{p['name']}{', ' + p['title'] if p.get('title') else ''}" for p in br.get("leadership") or []],
                  "tagline": br.get("tagline"), "services": br.get("services"), "service_areas": br.get("service_areas")},
        "scan_note": result["scan"].get("note"),
        "duration_s": (result.get("timings") or {}).get("total"),
    }


async def _callback(job: dict, payload: dict):
    url = (job["request"] or {}).get("callback_url")
    if not url:
        return "no callback"
    body = json.dumps(payload, default=str).encode()
    headers = {"content-type": "application/json"}
    if config.CALLBACK_SECRET:
        headers["X-Audit-Signature"] = hmac.new(config.CALLBACK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(url, content=body, headers=headers)
            if r.status_code < 300:
                return f"ok {r.status_code}"
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        await asyncio.sleep(5 * (attempt + 1))
    return f"failed: {last}"


def payload_for(job: dict, status: str, result: dict | None = None, error: str | None = None) -> dict:
    req = job["request"] or {}
    p = {"job_id": job["id"], "status": status, "cached": bool(job.get("cached_from")),
         "request": {k: req.get(k) for k in ("url", "business_name", "contact_name", "email", "phone", "location",
                                             "challenge", "source", "submission_id")},
         "passthrough": req.get("passthrough"), **links(job["id"], job["token"])}
    if result:
        p["summary"] = summary_of(result)
        p["email_subject"] = render.email_subject(result)
        _, _, email_path = result_paths(job)
        p["email_html"] = email_path.read_text() if email_path.exists() else render.email_html(result, p["report_url"])
        p["pdf_filename"] = f"AI-Visibility-Audit-{result['business']['name']}.pdf".replace(" ", "-").replace("/", "-")
    if error:
        p["error"] = error
    return p


async def run(job_id: str):
    job = get(job_id)
    if job["cached_from"]:
        src = get(job["cached_from"])
        jpath, _, _ = result_paths(job)
        result = json.loads(jpath.read_text()) if jpath.exists() else None
        if result:
            _update(job_id, status="done", step="Served from recent audit", finished_at=_now(), summary=summary_of(result))
            cb = await _callback(get(job_id), payload_for(get(job_id), "done", result))
            _update(job_id, callback_status=cb)
            return
        # cached files missing (e.g. redeploy without volume) -> run fresh
        _update(job_id, cached_from=None)
        job = get(job_id)
    async with _sem:
        _update(job_id, status="running", step="Starting")
        t0 = time.time()
        try:
            result = await asyncio.wait_for(
                audit.run(job["request"], progress=lambda m: _update(job_id, step=m)), timeout=15 * 60)
            jpath, ppath, epath = _paths(job_id)
            jpath.write_text(json.dumps(result, default=str))
            links_ = links(job_id, job["token"])
            epath.write_text(render.email_html(result, links_["report_url"]))
            pdf = await asyncio.to_thread(render.pdf, result)
            ppath.write_bytes(pdf)
            _update(job_id, status="done", step="Done", finished_at=_now(), summary=summary_of(result))
            log.info("job %s done in %.0fs score=%s", job_id, time.time() - t0, result["scores"]["overall_score"])
            cb = await _callback(get(job_id), payload_for(get(job_id), "done", result))
        except Exception as e:
            log.error("job %s failed: %s\n%s", job_id, e, traceback.format_exc())
            err = f"{type(e).__name__}: {e}"[:500]
            _update(job_id, status="failed", step="Failed", finished_at=_now(), error=err)
            cb = await _callback(get(job_id), payload_for(get(job_id), "failed", error=err))
        _update(job_id, callback_status=cb)
