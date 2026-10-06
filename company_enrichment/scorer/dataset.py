"""The answer key: one JSON line per company in company_enrichment/gt/ (schema in README "Dataset").

load() returns the rows; key() turns one row into the structure the rules read. Datasets are named by file stem:
'companies_public' (shipped, about half of every stratum) and 'companies_full_v<N>' (held back; not in this repo).
"""
import json
from pathlib import Path

from company_enrichment.scorer.normalize import li_parse, reg_domain, tick_norm

GT = Path(__file__).resolve().parents[1] / "gt"
DEFAULT = "companies_public"
GROUPS = ["us_public", "us_private"]


def path(name=DEFAULT):
    p = Path(name)
    if p.suffix == ".jsonl" and p.exists(): return p
    return GT / f"{p.stem if p.suffix else name}.jsonl"


def load(name=DEFAULT):
    p = path(name)
    if not p.exists(): raise SystemExit(f"dataset {p} not found (shipped: {sorted(x.stem for x in GT.glob('companies_*.jsonl'))})")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def key(row):
    """gt row -> the key the rules read (sets for matching, the parent object kept by identity)."""
    c, e = row["company"], row["expected"]
    li = e.get("linkedin")
    slugs = {s for s in (li_parse(x)[0] for x in (li or {}).get("slugs") or []) if s}
    ids = {str(x) for x in (li or {}).get("ids") or []}
    parent = c.get("parent") if isinstance(c.get("parent"), dict) else None
    hq = e.get("hq")
    loc = lambda v: {"city": v["city"], "state": v.get("state"), "country": v.get("country"),
                     "full_credit": bool(v.get("full_credit"))}
    emp, fo, tk, rv, ind = e.get("employees"), e.get("founded"), e.get("ticker"), e.get("revenue"), e.get("industry") or {}
    return {"id": row["id"], "group": row["group"], "name": c["name"], "names": c.get("names") or [c["name"]],
            "domains": {reg_domain(d) for d in [row["input"]["domain"]] + list(c.get("domains") or []) if d} - {None},
            "hq": loc(hq) if hq else None, "hq_alts": [loc(a) for a in (hq or {}).get("alternatives") or []],
            "employees": (float(emp["low"]), float(emp["high"])) if emp else None,
            "founded": int(fo["year"]) if fo and fo.get("year") is not None else None,
            "linkedin": {"slugs": slugs, "ids": ids} if (slugs or ids) else None,
            "ticker": ({tick_norm(x) for x in tk} - {None}) or None if tk else None,
            "revenue": {"value": float(rv["usd"]), "prior": rv.get("prior_usd")} if rv else None,
            # no description and no pre-registered list: nothing to grade an industry against, so not graded
            "industry": {"best_fit": set(ind.get("best_fit") or []), "either_best_fit": set(ind.get("either_best_fit") or []),
                         "broad_ok": set(ind.get("broad_ok") or []), "description": ind.get("description")}
            if (ind.get("description") or ind.get("best_fit") or ind.get("either_best_fit") or ind.get("broad_ok")) else None,
            "parent": parent, "others": ([parent] if parent else []) + [o for o in c.get("known_other") or [] if isinstance(o, dict)]}
