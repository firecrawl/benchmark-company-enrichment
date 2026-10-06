#!/usr/bin/env python3
"""Score saved runs against the answer key: the module that produced every number in the README.

    company-enrichment-report                                    # every run under company_enrichment/runs/
    company-enrichment-report --label apollo,ocean --out results.json
    company-enrichment-report --dataset companies_full_v2        # the held-back set (maintainers only)

Per company, provider and field the rules in scorer/rules.py give 1, 0.5 (0.75 for industry) or 0. Per company group
(US public, US private), a provider's score is its points over the graded slots; each score is the equal-weight mean of
the two groups. The common score (the headline) uses the four fields scored by code alone (rules.COMMON); the
all-fields score adds the extra fields: industry, ticker and revenue. 95% intervals come from a company bootstrap
stratified by group (the same resampled companies for every provider), paired differences from the same resamples with
a Holm correction over every pair of providers shown.
Only companies every scored provider was run on count.
"""
import argparse
import csv
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from company_enrichment.harness import providers  # noqa: E402
from company_enrichment.harness.records import answered, record  # noqa: E402
from company_enrichment.scorer import dataset, industry, rules  # noqa: E402
from company_enrichment.scorer.metro import Metro  # noqa: E402

RUNS = ROOT / "runs"
RESAMPLES, SEED = 5000, 2026


# ---------------------------------------------------------------- loading runs
def find_runs(root=RUNS, labels=None):
    """{label: run dir}: the newest run per label (or only the labels asked for)."""
    out = {}
    for d in sorted(Path(root).glob("*/meta.json")):
        meta = json.loads(d.read_text())
        if labels and meta["label"] not in labels: continue
        out[meta["label"]] = d.parent
    if labels and set(labels) - set(out): raise SystemExit(f"no run found for {sorted(set(labels) - set(out))} under {root}")
    return out


def load_run(d):
    meta = json.loads((Path(d) / "meta.json").read_text())
    recs = {p.stem: json.loads(p.read_text()) for p in (Path(d) / "records").glob("*.json")}
    return meta, recs


def system_name(meta):
    s = providers.SYSTEMS.get(meta["provider"])
    return s["name"] if s and meta["label"] == meta["provider"] else meta["label"]


# ---------------------------------------------------------------- grading
def grade(gt_rows, runs, metro, grades):
    """runs: {system: (provider id, {company id: saved record})} -> (verdict rows, meta)."""
    keys = {r["id"]: dataset.key(r) for r in gt_rows}
    board = list(runs)
    recs = {}
    for s, (prov, saved) in runs.items():
        for cid, rr in saved.items():
            recs[(cid, s)] = record(prov, rr.get("response")) if rr.get("http_status") == 200 else None
    support = {s: {f: any(answered(recs.get((c, s)), f) for c in keys) for f in rules.ALL} for s in board}
    scored, excluded = {}, {}
    for cid, K in keys.items():
        missing = [s for s in board if cid not in runs[s][1]]
        if missing: excluded[cid] = f"not run on {', '.join(missing)}"; continue
        scored[cid] = K
    rows, pending = [], {}
    for cid, K in scored.items():
        for s in board:
            R = recs[(cid, s)]
            wc = rules.wrong_company(R, K)
            for f in rules.ALL:
                v = {"company_id": cid, "group": K["group"], "system": s, "field": f, "status": None, "score": None,
                     "answered": answered(R, f), "wrong_company": bool(wc), "wc_reason": "; ".join(wc), "note": ""}
                if not rules.key_has(K, f): v["status"] = "no_key"
                elif not v["answered"]:  # an empty answer and a field the provider never returns both score 0
                    v["status"], v["score"] = ("empty" if support[s][f] else "not_offered"), 0.0
                else:
                    if f == "hq": sc, v["note"] = rules.score_hq(R, K, metro)
                    elif f == "employees":
                        val, v["note"] = rules.emp_value(R); sc = rules.emp_score(val, *K["employees"])
                    elif f == "founded": sc = rules.score_founded(R, K)
                    elif f == "linkedin": sc = rules.score_linkedin(R, K)
                    elif f == "ticker": sc = rules.score_ticker(R, K)
                    elif f == "revenue": sc = rules.score_revenue(R, K)
                    else:
                        sc, v["note"] = industry.score(R, K, grades)
                        if sc is None: pending[industry.pair_key(cid, R["industry"])] = industry.item(K, R["industry"])
                    v["score"] = sc
                    v["status"] = "ungraded" if sc is None else "correct" if sc == 1 else "wrong" if sc == 0 else "partial"
                    if wc and sc is not None: v["score"], v["status"] = 0.0, "wrong_company"
                rows.append(v)
    return rows, {"board": board, "keys": scored, "excluded": excluded, "support": support, "recs": recs, "pending": pending}


# ---------------------------------------------------------------- aggregation and statistics
def table(rows, systems, fields):
    """{system: {company: [points, slots]}} over the given fields."""
    t = {s: {} for s in systems}
    for r in rows:
        if r["system"] not in t or r["field"] not in fields: continue
        cell = t[r["system"]].setdefault(r["company_id"], [0.0, 0])
        if r["score"] is not None: cell[0] += r["score"]; cell[1] += 1
    return t


def summarize(tl, members):
    by = {}
    for g, cids in members.items():
        P = sum(tl.get(c, [0, 0])[0] for c in cids); S = sum(tl.get(c, [0, 0])[1] for c in cids)
        by[g] = P / S if S else None
    vals = [x for x in by.values() if x is not None]
    return {"score": sum(vals) / len(vals) if vals else None, "groups": by}


def bootstrap(t, systems, members, B, seed):
    """Company resamples, stratified by group; the same draw for every system."""
    rng = random.Random(seed)
    groups = [(g, c) for g, c in members.items() if c]
    arr = {s: {g: [(t[s].get(c, [0, 0])[0], t[s].get(c, [0, 0])[1]) for c in cids] for g, cids in groups} for s in systems}
    out = {s: [] for s in systems}
    for _ in range(B):
        idx = {g: [rng.randrange(len(c)) for _ in c] for g, c in groups}
        for s in systems:
            vals = []
            for g, _c in groups:
                a = arr[s][g]; P = sum(a[i][0] for i in idx[g]); S = sum(a[i][1] for i in idx[g])
                if S: vals.append(P / S)
            out[s].append(sum(vals) / len(vals) if vals else float("nan"))
    return out


def pct(xs, q):
    xs = sorted(x for x in xs if not math.isnan(x))
    if not xs: return None
    k = (len(xs) - 1) * q; f = math.floor(k)
    return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)


def compare(boot, point_est, systems):
    """Paired bootstrap differences for every pair, Holm-corrected; rank intervals."""
    pairs, B = [], len(next(iter(boot.values()))) if boot else 0
    for i, a in enumerate(systems):
        for b in systems[i + 1:]:
            d = [x - y for x, y in zip(boot[a], boot[b]) if not (math.isnan(x) or math.isnan(y))]
            if not d or point_est[a] is None or point_est[b] is None: continue
            le, ge = sum(x <= 0 for x in d) / len(d), sum(x >= 0 for x in d) / len(d)
            pairs.append({"a": a, "b": b, "diff": point_est[a] - point_est[b], "ci95": [pct(d, .025), pct(d, .975)],
                          "p": min(1.0, 2 * min(le, ge))})
    order = sorted(range(len(pairs)), key=lambda i: pairs[i]["p"])
    m, run = len(pairs), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * pairs[i]["p"])); pairs[i]["p_holm"] = run
        pairs[i]["significant"] = run < 0.05
    ranks = {s: [] for s in systems}
    for k in range(B):
        vals = sorted(((boot[s][k], s) for s in systems if not math.isnan(boot[s][k])), reverse=True)
        for pos, (_, s) in enumerate(vals): ranks[s].append(pos + 1)
    rank_ci = {s: [pct([float(x) for x in ranks[s]], .025), pct([float(x) for x in ranks[s]], .975)] for s in systems if ranks[s]}
    return pairs, rank_ci


def members_of(keys):
    m = {g: sorted(c for c, K in keys.items() if K["group"] == g) for g in dataset.GROUPS}
    m.update({g: sorted(c for c, K in keys.items() if K["group"] == g) for g in {K["group"] for K in keys.values()} - set(dataset.GROUPS)})
    return {g: c for g, c in m.items() if c}


def r4(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 6)


def aggregate(rows, meta, resamples=RESAMPLES, seed=SEED, systems_meta=None):
    board, keys = meta["board"], meta["keys"]
    members = members_of(keys)
    res = {"systems": {s: {**((systems_meta or {}).get(s) or {})} for s in board}, "pairwise": {}}
    for name, fields in (("common", rules.COMMON), ("all_fields", rules.ALL)):
        t = table(rows, board, fields)
        boot = bootstrap(t, board, members, resamples, seed)
        pts = {s: summarize(t[s], members)["score"] for s in board}
        pairs, rank_ci = compare(boot, pts, board)
        for s in board:
            sm = summarize(t[s], members)
            res["systems"][s][name] = {"score": r4(sm["score"]), "ci95": [r4(pct(boot[s], .025)), r4(pct(boot[s], .975))],
                                       "rank_ci95": rank_ci.get(s), "groups": {g: r4(v) for g, v in sm["groups"].items()}}
        res["pairwise"][name] = [{**p, "diff": r4(p["diff"]), "ci95": [r4(x) for x in p["ci95"]], "p": r4(p["p"]),
                                  "p_holm": r4(p["p_holm"])} for p in pairs]
    for f in rules.ALL:  # per field: same aggregation and the same resampled companies
        t = table(rows, board, [f])
        boot = bootstrap(t, board, members, resamples, seed)
        for s in board:
            rs = [r for r in rows if r["system"] == s and r["field"] == f and r["score"] is not None]
            ans = [r for r in rs if r["answered"]]
            res["systems"][s].setdefault("fields", {})[f] = {
                "score": r4(summarize(t[s], members)["score"]), "ci95": [r4(pct(boot[s], .025)), r4(pct(boot[s], .975))],
                "offered": meta["support"][s][f], "graded": len(rs), "coverage": r4(len(ans) / len(rs)) if rs else None,
                "accuracy_when_answered": r4(sum(r["score"] for r in ans) / len(ans)) if ans else None}
    for s in board:
        rs = [r for r in rows if r["system"] == s and r["field"] in rules.COMMON and r["score"] is not None]
        ans = [r for r in rs if r["answered"]]
        res["systems"][s].update(
            coverage=r4(len(ans) / len(rs)) if rs else None,
            accuracy_when_answered=r4(sum(r["score"] for r in ans) / len(ans)) if ans else None,
            wrong_company_records=sum(1 for c, K in keys.items() if rules.wrong_company(meta["recs"].get((c, s)), K)))
    res["companies_scored"] = {g: len(c) for g, c in members.items()}
    res["excluded"] = meta["excluded"]
    res["ci"] = {"method": "percentile bootstrap", "resamples": resamples, "seed": seed, "level": 0.95,
                 "clusters": "companies, resampled within each group (US public, US private) at fixed size",
                 "pairwise": "paired differences on the same resamples, Holm-corrected over every pair of systems shown"}
    res["aggregation"] = "per group: points / graded slots; score = equal-weight mean of the two groups"
    return res


# ---------------------------------------------------------------- output
def P(x, d=1):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def markdown(res):
    """The README's results tables."""
    order = sorted(res["systems"], key=lambda s: -(res["systems"][s]["common"]["score"] or -1))
    tag = lambda s: {"alexandria": " `[alexandria]`", "soon": " `[soon]`"}.get(res["systems"][s].get("tag"), "")
    pub = all(res["systems"][s].get("public_sample") for s in order)
    n_pub = res["dataset"].get("public_sample") if isinstance(res.get("dataset"), dict) else None
    L = ["| provider | common score | 95% CI | all fields | 95% CI | coverage | accuracy when answered |"
         + (f" public sample (n={n_pub}) |" if pub else ""), "|---|---|---|---|---|---|---|" + ("---|" if pub else "")]
    best = order[0]
    for s in order:
        x = res["systems"][s]; c, a = x["common"], x["all_fields"]
        b = (lambda v: f"**{v}**") if s == best else (lambda v: v)
        L.append(f"| {s}{tag(s)} | {b(P(c['score']))} | [{P(c['ci95'][0])}, {P(c['ci95'][1])}] | {P(a['score'])} | "
                 f"[{P(a['ci95'][0])}, {P(a['ci95'][1])}] | {P(x['coverage'], 0)} | {P(x['accuracy_when_answered'], 0)} |"
                 + (f" {P(x['public_sample']['common']['score'])} |" if pub else ""))
    any_sys = res["systems"][order[0]]["fields"]
    n = res["dataset"]["companies"] if isinstance(res.get("dataset"), dict) else sum(res["companies_scored"].values())
    # each field with the number of companies it is graded on (a field with no primary source is graded for no one)
    extra = lambda f: "" if f in rules.COMMON else "extra, "   # extra fields: shown, not in the common score
    L += ["", "| provider | " + " | ".join(f"{f} ({extra(f)}{any_sys[f]['graded']} of {n})" for f in rules.ALL) + " |",
          "|---" * (1 + len(rules.ALL)) + "|"]
    top = {f: max([(res["systems"][s]["fields"][f]["score"] or 0) for s in order if res["systems"][s]["fields"][f]["offered"]], default=None) for f in rules.ALL}
    for s in order:
        cells = []
        for f in rules.ALL:
            fx = res["systems"][s]["fields"][f]
            v = "not offered" if not fx["offered"] else P(fx["score"], 0)
            cells.append(f"**{v}**" if fx["offered"] and fx["score"] == top[f] else v)
        L.append(f"| {s} | " + " | ".join(cells) + " |")
    pair = {frozenset((p["a"], p["b"])): p for p in res["pairwise"]["common"]}
    tied = [s for s in order[1:] if not pair[frozenset((best, s))]["significant"]]
    if tied:
        L += ["", f"On the common score {best}'s lead over {' and '.join(tied)} is not statistically significant "
                  f"(paired bootstrap, Holm-corrected over {len(pair)} pair{'s' if len(pair) != 1 else ''}): the data cannot separate them."]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default=str(RUNS), help="directory holding run folders (default company_enrichment/runs)")
    ap.add_argument("--label", help="comma list of run labels to score (default: the newest run of every label)")
    ap.add_argument("--dataset", default=dataset.DEFAULT)
    ap.add_argument("--resamples", type=int, default=RESAMPLES)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", help="write results JSON here")
    ap.add_argument("--verdicts", help="write one CSV row per company x provider x field here")
    ap.add_argument("--markdown", action="store_true", help="print the README tables")
    ap.add_argument("--geocode", action="store_true", help="look up US locations missing from the metro table (free, slow)")
    ap.add_argument("--judge", action="store_true", help="grade new industry labels with the five judges (paid)")
    a = ap.parse_args()
    from company_enrichment.run_eval import load_env
    load_env()
    gt = dataset.load(a.dataset)
    runs_root = Path(a.runs)
    found = find_runs(runs_root, a.label.split(",") if a.label else None)
    if not found: raise SystemExit(f"no runs under {runs_root}: run company-enrichment --provider <id> first")
    runs, smeta = {}, {}
    for label, d in found.items():
        meta, recs = load_run(d)
        name = system_name(meta)
        runs[name] = (meta["provider"], recs)
        sm = providers.SYSTEMS.get(meta["provider"], {}) if name != meta["label"] else {}
        smeta[name] = {"provider": meta["provider"], "tag": sm.get("tag"), "via": sm.get("via", "external endpoint"),
                       "run": d.name}
    votes_local = runs_root / "industry_votes_local.jsonl"
    grade_files = sorted(dataset.GT.glob("industry_grades_*.jsonl")) + [votes_local]
    metro = Metro(runs_root / "hq_metro_local.json")
    rows, meta = grade(gt, runs, metro, industry.load_grades(grade_files))
    if meta["pending"]:
        items = sorted(meta["pending"].values(), key=lambda x: x["id"])
        if a.judge:
            industry.judge(items, votes_local)
            rows, meta = grade(gt, runs, Metro(runs_root / "hq_metro_local.json"), industry.load_grades(grade_files))
        else:
            print(f"{len(items)} industry labels have no grade yet and are left out of the score; "
                  f"add --judge to grade them (paid, about $0.02 a label)", file=sys.stderr)
    if metro.pending and a.geocode:
        print(f"geocoded {metro.fetch_pending()} locations -> {metro.local}; run the report again to use them", file=sys.stderr)
    elif metro.pending:
        print(f"{len(metro.pending)} US locations are not in the metro table (only an exact city match can score them); "
              f"add --geocode to look them up", file=sys.stderr)
    res = aggregate(rows, meta, a.resamples, a.seed, smeta)
    res = {"benchmark": "Company Enrichment", "dataset": dataset.path(a.dataset).stem,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "fields": {"common": rules.COMMON, "all": rules.ALL}, "scorer": "company_enrichment.scorer.report_metrics", **res}
    print(markdown(res))
    print(f"\ncompanies scored: {res['companies_scored']}; excluded: {len(res['excluded'])}", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n"); print(f"wrote {a.out}", file=sys.stderr)
    if a.verdicts:
        cols = ["company_id", "group", "system", "field", "status", "score", "answered", "wrong_company", "wc_reason", "note"]
        with open(a.verdicts, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows({k: r[k] for k in cols} for r in rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
