"""Structured data: extract every JSON-LD block, validate the types that matter for local AI visibility."""
from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

LOCAL_TYPES = {
    "LocalBusiness", "ProfessionalService", "HomeAndConstructionBusiness", "Plumber", "Electrician",
    "HVACBusiness", "RoofingContractor", "GeneralContractor", "LegalService", "Attorney", "Dentist",
    "MedicalBusiness", "MedicalClinic", "Physician", "Chiropractor", "Optician", "AutoRepair",
    "AnimalShelter", "PetStore", "Restaurant", "FoodEstablishment", "RealEstateAgent", "AccountingService",
    "FinancialService", "InsuranceAgency", "HealthAndBeautyBusiness", "SportsActivityLocation",
    "ExerciseGym", "ChildCare", "EducationalOrganization", "FuneralHome", "Store", "MovingCompany",
    "HousePainter", "Locksmith", "EmergencyService", "Veterinary", "VeterinaryCare", "LodgingBusiness",
    "EntertainmentBusiness", "AutomotiveBusiness", "TravelAgency", "EmploymentAgency", "Library",
}

# Required/recommended properties we check per type
REQUIRED = {
    "local": ["name", "address", "telephone", "url"],
    "local_recommended": ["openingHoursSpecification|openingHours", "geo", "image|logo", "sameAs",
                          "areaServed", "priceRange", "aggregateRating"],
    "Organization": ["name", "url", "logo"],
    "FAQPage": ["mainEntity"],
    "Service": ["name", "provider|areaServed"],
    "Person": ["name", "jobTitle|worksFor"],
}


def _walk(obj, out: list):
    if isinstance(obj, dict):
        if "@type" in obj:
            out.append(obj)
        for k, v in obj.items():
            if k == "@context":
                continue
            _walk(v, out)
    elif isinstance(obj, list):
        for i in obj:
            _walk(i, out)


def _types(node: dict) -> list[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return [str(x) for x in t]
    return [str(t)] if t else []


def extract(html: str) -> dict:
    """Returns {nodes:[...], types:set, errors:int, microdata:bool, rdfa:bool}."""
    soup = BeautifulSoup(html or "", "lxml")
    nodes, errors = [], 0
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # common breakage: trailing commas / control chars
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.replace("\n", " ")))
            except json.JSONDecodeError:
                errors += 1
                continue
        _walk(data, nodes)
    types = set()
    for n in nodes:
        types.update(_types(n))
    return {
        "nodes": nodes,
        "types": types,
        "errors": errors,
        "microdata": bool(soup.find(attrs={"itemtype": True})),
        "rdfa": bool(soup.find(attrs={"typeof": True})),
    }


def _has(node: dict, spec: str) -> bool:
    return any(node.get(k) not in (None, "", [], {}) for k in spec.split("|"))


def merge(pages_schema: list[dict]) -> dict:
    nodes, types, errors = [], set(), 0
    for s in pages_schema:
        nodes += s["nodes"]
        types |= s["types"]
        errors += s["errors"]
    return {"nodes": nodes, "types": types, "errors": errors}


def analyze(merged: dict) -> dict:
    nodes = merged["nodes"]
    local_nodes = [n for n in nodes if set(_types(n)) & LOCAL_TYPES]
    org_nodes = [n for n in nodes if "Organization" in _types(n)]
    best_local = max(local_nodes, key=lambda n: sum(_has(n, s) for s in REQUIRED["local"] + REQUIRED["local_recommended"]),
                     default=None)
    local_missing_req = [s for s in REQUIRED["local"] if best_local is None or not _has(best_local, s)]
    local_missing_rec = [s.split("|")[0] for s in REQUIRED["local_recommended"]
                         if best_local is None or not _has(best_local, s)]
    same_as = []
    for n in local_nodes + org_nodes:
        sa = n.get("sameAs")
        if isinstance(sa, str):
            same_as.append(sa)
        elif isinstance(sa, list):
            same_as += [x for x in sa if isinstance(x, str)]
    types = merged["types"]
    return {
        "types": sorted(types),
        "errors": merged["errors"],
        "has_local": bool(local_nodes),
        "local_type": _types(best_local)[0] if best_local else None,
        "local_missing_required": local_missing_req,
        "local_missing_recommended": local_missing_rec,
        "has_org": bool(org_nodes) or bool(local_nodes),
        "has_faq": "FAQPage" in types,
        "has_service": "Service" in types or any("Service" in t for t in types if t != "ProfessionalService"),
        "has_review": bool({"Review", "AggregateRating"} & types) or any(n.get("aggregateRating") for n in local_nodes),
        "has_person": "Person" in types,
        "has_breadcrumb": "BreadcrumbList" in types,
        "has_website": "WebSite" in types,
        "same_as": sorted(set(same_as)),
        "best_local": best_local,
        "persons": [n for n in nodes if "Person" in _types(n)],
    }
