"""The scoring data model. Every point in the report traces to one Check with evidence."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

PASS, PARTIAL, FAIL, NOT_MEASURED, INFO = "pass", "partial", "fail", "not_measured", "info"


@dataclass
class Check:
    id: str
    section: str
    label: str
    points: float
    earned: float = 0.0
    status: str = NOT_MEASURED
    evidence: str = ""
    fix: str = ""          # plain-English fix, shown when not passing
    sells: str = ""        # which IA offer fixes it

    def score(self, earned: float, evidence: str, fix: str = "") -> "Check":
        self.earned = round(max(0.0, min(self.points, earned)), 2)
        if self.earned >= self.points:
            self.status = PASS
        elif self.earned > 0:
            self.status = PARTIAL
        else:
            self.status = FAIL
        self.evidence = evidence
        if self.status != PASS:
            self.fix = fix or self.fix
        return self

    def skip(self, why: str) -> "Check":
        self.status = NOT_MEASURED
        self.earned = 0.0
        self.evidence = why
        return self

    def to_dict(self) -> dict:
        return asdict(self)


# Section definitions: key -> (title, max points, what IA sells against it)
SECTIONS: dict[str, dict] = {
    "ai_answers":   {"title": "AI Answers",            "points": 25, "sells": "Immersive Authority System"},
    "ai_readable":  {"title": "AI-Readable Website",   "points": 20, "sells": "Authority Site"},
    "content":      {"title": "Answer Content",        "points": 15, "sells": "Knowledge Hub"},
    "google":       {"title": "Google Footprint",      "points": 15, "sells": "Distribution Engine (GBP)"},
    "market":       {"title": "Local Market Position", "points": 15, "sells": "Authority Engine"},
    "distribution": {"title": "Distribution Reach",    "points": 10, "sells": "Distribution Engine"},
}


@dataclass
class SectionScore:
    key: str
    title: str
    points: float
    measured_points: float
    earned: float
    score: int | None       # 0-100 over measured checks, None if nothing measured
    sells: str
    checks: list[dict] = field(default_factory=list)


def grade(score: int | None) -> str:
    if score is None:
        return "—"
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"


def band(score: int | None) -> str:
    if score is None:
        return "Not measured"
    if score >= 85:
        return "Leading"
    if score >= 70:
        return "Strong"
    if score >= 50:
        return "Developing"
    if score >= 30:
        return "Weak"
    return "Invisible"


def roll_up(checks: list[Check]) -> dict:
    """Section scores are normalized over what we could measure, then weighted to 100."""
    sections = []
    total_weight_measured = 0.0
    weighted = 0.0
    for key, meta in SECTIONS.items():
        cs = [c for c in checks if c.section == key]
        measured = [c for c in cs if c.status not in (NOT_MEASURED, INFO)]
        mp = sum(c.points for c in measured)
        earned = sum(c.earned for c in measured)
        score = round(100 * earned / mp) if mp > 0 else None
        # Only count a section toward the overall if we measured at least half of it.
        coverage = mp / sum(c.points for c in cs) if cs and sum(c.points for c in cs) else 0
        if score is not None and coverage >= 0.5:
            total_weight_measured += meta["points"]
            weighted += meta["points"] * score / 100
        sections.append(SectionScore(
            key=key, title=meta["title"], points=meta["points"], measured_points=mp,
            earned=round(earned, 2), score=score, sells=meta["sells"],
            checks=[c.to_dict() for c in cs],
        ))
    overall = round(100 * weighted / total_weight_measured) if total_weight_measured else None
    coverage = round(100 * total_weight_measured / sum(m["points"] for m in SECTIONS.values()))
    return {
        "overall_score": overall,
        "overall_grade": grade(overall),
        "overall_band": band(overall),
        "coverage_pct": coverage,
        "sections": [asdict(s) | {"grade": grade(s.score), "band": band(s.score)} for s in sections],
    }
