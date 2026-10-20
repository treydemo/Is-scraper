"""AI Answers: ask the real AI engines (with live web search) the questions buyers ask, and record
whether this business gets named, where, and who gets named instead. Every answer is kept as evidence."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from .. import config, llm
from .google import name_tokens

_PARSE_SYSTEM = ("You read AI assistant answers and list the businesses they recommend or name, in the "
                 "order they first appear. Return only JSON. Do not add businesses that are not in the text.")

_PARSE_PROMPT = """Target business: "{name}" (website {domain}).

For each answer below, return:
{{"answers": [{{"id": int, "businesses": [str], "target_named": bool, "target_position": int|null}}]}}
- "businesses": names of specific local businesses named in the answer (not directories like Yelp/Angi/Google).
- "target_named": true only if the target business itself is named (name or website), not a similarly named different business.
- "target_position": 1-based position of the target in "businesses", or null.

{answers}"""

_FACTS_SYSTEM = ("You compare what an AI assistant said about a business to the facts on the business's "
                 "own website. Judge only what is stated. Return only JSON.")

_FACTS_PROMPT = """Facts from the website:
{facts}

AI answers about the business:
{answers}

Return:
{{"verdicts": [{{"engine": str, "knows_business": bool, "accuracy": "accurate"|"partly_wrong"|"wrong"|"no_info",
"errors": [str], "summary": "one sentence, plain English"}}]}}
"errors" lists specific claims that contradict the website (wrong phone, wrong city, wrong services, says it closed, etc.)."""


def _mentions(text: str, name: str, domain: str) -> bool:
    t = (text or "").lower()
    if domain and domain.lower() in t:
        return True
    toks = name_tokens(name)
    if not toks:
        return False
    if name.lower().strip() and name.lower().strip() in t:
        return True
    # all distinctive tokens of the business name appear close together
    words = re.findall(r"[a-z0-9]+", t)
    span = len(toks) + 4
    for i in range(len(words)):
        if toks.issubset(set(words[i:i + span])):
            return True
    return False


def _same_business(a: str, b: str) -> bool:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta or len(ta & tb) / min(len(ta), len(tb)) >= 0.75


async def run(name: str, domain: str, city: str, region: str, questions: list[str],
              site_facts: str, competitor_names: list[str]) -> dict:
    engines = llm.configured_engines()
    started = datetime.now(timezone.utc).isoformat()
    if not engines:
        return {"engines": [], "error": "no AI engine keys configured", "queries": [], "started_at": started}
    location = {"city": city, "region": region, "country": "US"}
    brand_q = f"What can you tell me about {name}{' in ' + city if city else ''}? Are they any good?"
    sem = asyncio.Semaphore(6)

    async def ask(engine: str, q: str, kind: str):
        async with sem:
            text, err = await llm.ENGINES[engine](q, location)
            return {"engine": engine, "question": q, "kind": kind, "answer": text or "", "error": err,
                    "asked_at": datetime.now(timezone.utc).isoformat()}

    tasks = [ask(e, q, "buyer") for e in engines for q in questions]
    tasks += [ask(e, brand_q, "brand") for e in engines]
    rows = await asyncio.gather(*tasks)
    buyer = [r for r in rows if r["kind"] == "buyer" and r["answer"]]
    brand = [r for r in rows if r["kind"] == "brand" and r["answer"]]

    # Parse who got named, in batches
    parsed: dict[int, dict] = {}
    for start in range(0, len(buyer), 8):
        batch = buyer[start:start + 8]
        block = "\n\n".join(f"### id {start + i}\nQuestion: {r['question']}\nAnswer:\n{r['answer'][:3500]}"
                            for i, r in enumerate(batch))
        data, err = await llm.claude_json(_PARSE_SYSTEM, _PARSE_PROMPT.format(name=name, domain=domain, answers=block),
                                          model=config.CLAUDE_FAST_MODEL, max_tokens=3000)
        if data and isinstance(data.get("answers"), list):
            for a in data["answers"]:
                if isinstance(a, dict) and isinstance(a.get("id"), int):
                    parsed[a["id"]] = a
    for i, r in enumerate(buyer):
        p = parsed.get(i, {})
        det = _mentions(r["answer"], name, domain)
        r["target_named"] = bool(det or p.get("target_named"))
        r["target_position"] = p.get("target_position") if r["target_named"] else None
        r["businesses_named"] = [b for b in (p.get("businesses") or []) if isinstance(b, str)][:12]
        r["competitors_named"] = [c for c in competitor_names
                                  if _mentions(r["answer"], c, "") or
                                  any(_same_business(c, b) for b in r["businesses_named"])]

    # Brand knowledge accuracy
    verdicts = []
    if brand and site_facts:
        answers = "\n\n".join(f"[{r['engine']}]\n{r['answer'][:3000]}" for r in brand)
        data, _ = await llm.claude_json(_FACTS_SYSTEM, _FACTS_PROMPT.format(facts=site_facts, answers=answers),
                                        max_tokens=2000)
        if data and isinstance(data.get("verdicts"), list):
            verdicts = [v for v in data["verdicts"] if isinstance(v, dict)]

    # Share of voice
    total = len(buyer)
    named = sum(1 for r in buyer if r["target_named"])
    by_engine = {}
    for e in engines:
        er = [r for r in buyer if r["engine"] == e]
        by_engine[e] = {"asked": len(er), "named": sum(1 for r in er if r["target_named"]),
                        "errors": sum(1 for r in rows if r["engine"] == e and r["error"])}
    comp_sov = {c: sum(1 for r in buyer if c in r["competitors_named"]) for c in competitor_names}
    # Most-recommended businesses overall (whoever AI is actually sending customers to)
    tally: dict[str, int] = {}
    for r in buyer:
        for b in dict.fromkeys(r["businesses_named"]):
            tally[b] = tally.get(b, 0) + 1
    top_named = sorted(tally.items(), key=lambda kv: -kv[1])[:8]

    return {
        "engines": engines,
        "questions": questions,
        "brand_question": brand_q,
        "started_at": started,
        "answers_total": total,
        "answers_named": named,
        "share_of_voice": round(100 * named / total) if total else None,
        "by_engine": by_engine,
        "competitor_mentions": comp_sov,
        "competitor_sov": {c: round(100 * n / total) for c, n in comp_sov.items()} if total else {},
        "top_named": [{"name": n, "count": c, "pct": round(100 * c / total),
                       "is_target": _same_business(n, name) or _mentions(n, name, domain)}
                      for n, c in top_named] if total else [],
        "brand_answers": brand,
        "brand_verdicts": verdicts,
        "rows": buyer,
        "failed": [r for r in rows if r["error"]],
    }
