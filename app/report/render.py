"""Render the report (HTML → PDF via WeasyPrint) and the delivery email."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config
from ..checks.site import SEARCH_BOTS

_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=select_autoescape(["html"]))


def _wrap(v):
    return v


def _excerpt(text: str, n: int = 340) -> str:
    t = re.sub(r"\[(\d+|[^\]]{0,4})\]", "", text or "")           # citation markers
    t = re.sub(r"[*#_`>]+", "", t)
    t = re.sub(r"\(https?://[^)]+\)", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " …"


def _receipts(ai: dict, k: int = 3) -> list[dict]:
    rows = ai.get("rows") or []
    missed = [r for r in rows if not r.get("target_named") and r.get("businesses_named")]
    hit = [r for r in rows if r.get("target_named")]
    pick, seen_q = [], set()
    for r in missed + hit + rows:
        if r["question"] in seen_q:
            continue
        seen_q.add(r["question"])
        pick.append({**r, "excerpt": _excerpt(r["answer"])})
        if len(pick) >= k:
            break
    return pick


def context(result: dict) -> dict:
    when = datetime.fromisoformat(result["audited_at"])
    s = result["scores"]
    return {
        "b": _wrap(result["business"]), "s": _wrap(s), "n": _wrap(result["narrative"]),
        "brand": _wrap(result["brand"]), "ai": _wrap(result.get("ai") or {}), "market": _wrap(result.get("market") or {}),
        "g": _wrap(result.get("google") or {}), "site": _wrap(result.get("site") or {}), "scan": _wrap(result["scan"]),
        "unscored": _wrap(result["unscored"]), "sec_by": {x["key"]: _wrap(x) for x in s["sections"]},
        "receipts": [_wrap(r) for r in _receipts(result.get("ai") or {})],
        "search_bots": SEARCH_BOTS, "booking_url": config.BOOKING_URL,
        "date": when.strftime("%B %-d, %Y"), "date_full": when.strftime("%B %-d, %Y at %H:%M UTC"),
    }


def html(result: dict) -> str:
    return _env.get_template("report.html").render(**context(result))


def pdf(result: dict) -> bytes:
    from weasyprint import HTML
    return HTML(string=html(result), base_url=str(Path(__file__).parent)).write_pdf()


def email_html(result: dict, report_url: str) -> str:
    return _env.get_template("email.html").render(**context(result), report_url=report_url)


def email_subject(result: dict) -> str:
    sc = result["scores"]["overall_score"]
    name = result["business"]["name"]
    return (f"{name}: your AI Visibility score is {sc}/100" if sc is not None
            else f"{name}: your AI Visibility & Authority audit")
