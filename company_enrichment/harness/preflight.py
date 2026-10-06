"""preflight.py: before a run, check that each provider still answers in the shape records.py reads.

    python3 -m company_enrichment.harness.preflight apollo ocean
    company-enrichment --provider apollo            # runs it first unless --skip-preflight

Calls the provider for the probe companies in fields_manifest.json (well-known companies that are not in the answer key)
and checks every 'required' path: each must hold a value for at least one probe. A provider that renamed or dropped
a field fails here, loudly, instead of scoring 0 on that field for every company. Paths the manifest does not know are
listed too, so a new field is noticed. Costs one lookup per probe (three per provider). A passing check is remembered
for 24 hours in runs/preflight_<provider>.json.
"""
import json
import re
import sys
import time
from pathlib import Path

from company_enrichment.harness import providers

MANIFEST = json.loads((Path(__file__).with_name("fields_manifest.json")).read_text())
RUNS = Path(__file__).resolve().parents[1] / "runs"
FRESH_S = 24 * 3600


def values(obj, path):
    """All values at a path ('a.b[0].c', '[]' = every list item); missing steps yield nothing."""
    cur = [obj]
    for part in re.findall(r"[^.\[\]]+|\[\d*\]", path):
        nxt = []
        for x in cur:
            if part == "[]": nxt += x if isinstance(x, list) else []
            elif part.startswith("["): nxt += [x[int(part[1:-1])]] if isinstance(x, list) and len(x) > int(part[1:-1]) else []
            elif isinstance(x, dict) and part in x: nxt.append(x[part])
        cur = nxt
    return cur


def present(v):
    return v not in (None, "", [], {}, 0)


def leaf_paths(obj, prefix=""):
    """Every leaf path of a response, lists written as [] (to spot fields the manifest does not know)."""
    if isinstance(obj, dict):
        out = set()
        for k, v in obj.items(): out |= leaf_paths(v, f"{prefix}.{k}" if prefix else k)
        return out or {prefix}
    if isinstance(obj, list):
        out = set()
        for v in obj: out |= leaf_paths(v, f"{prefix}[]")
        return out or {prefix}
    return {prefix}


def check(provider, probes=None, responses=None):
    """-> report dict with ok, missing (required paths empty on every probe), failed probes, unknown paths."""
    spec = MANIFEST["providers"][provider]
    probes = probes or MANIFEST["probe_domains"]
    if responses is None:
        responses = {d: r for d, r in providers.call(provider, probes).items()}
    got, failed, seen = {}, [], set()
    for d in probes:
        r = responses.get(d)
        status, body = (r.http_status, r.response) if hasattr(r, "http_status") else (200, r)
        if status != 200 or not body: failed.append(f"{d}: HTTP {status}" + ("" if body else ", empty answer")); continue
        if provider == "pdl" and isinstance(body, dict) and "data" in body and "id" not in body: body = body["data"]
        got[d] = body
        seen |= leaf_paths(body)
    missing = [p for p in spec["required"] if not any(present(v) for b in got.values() for v in values(b, p))]
    known = {re.sub(r"\[\d+\]", "[]", p) for p in spec["required"] + spec["optional"]}
    unknown = sorted(p for p in seen if p and not any(p == k or p.startswith(k + ".") or p.startswith(k + "[]") for k in known))
    return {"provider": provider, "ok": bool(got) and not missing, "probes": probes, "answered": sorted(got),
            "failed": failed, "missing_required": missing, "unknown_paths": unknown,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def run(provider, runs=RUNS, force=False):
    """Preflight with a 24-hour memory of a pass. Exits on failure."""
    if not MANIFEST["providers"].get(provider, {}).get("required"): return {"provider": provider, "ok": True, "skipped": "nothing to check"}
    f = Path(runs) / f"preflight_{provider}.json"
    if not force and f.exists():
        prev = json.loads(f.read_text())
        if prev.get("ok") and time.time() - f.stat().st_mtime < FRESH_S: return prev
    rep = check(provider)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rep, indent=1))
    print(f"preflight {provider}: {'ok' if rep['ok'] else 'FAILED'} ({len(rep['answered'])}/{len(rep['probes'])} probes answered, "
          f"{len(rep['unknown_paths'])} fields not read by the benchmark)", flush=True)
    if not rep["ok"]:
        sys.exit(f"preflight failed for {provider}: missing {rep['missing_required']} failed {rep['failed']}. "
                 "Fix the provider's key or update records.py and fields_manifest.json (a new benchmark version) before running.")
    return rep


if __name__ == "__main__":
    from company_enrichment.run_eval import load_env
    load_env()
    for p in sys.argv[1:] or sorted(k for k, v in MANIFEST["providers"].items() if v["required"]): run(p, force=True)
