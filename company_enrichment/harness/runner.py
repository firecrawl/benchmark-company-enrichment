"""Send every company's domain to one provider and save each raw answer.

Layout: runs/<UTC timestamp>-<label>/meta.json and runs/<...>/records/<company id>.json, one file per company with
{company_id, provider, domain, http_status, latency_ms, cost, error, route, ran_at, response}. Runs are the artifact of
YOUR run, not of the repo: runs/ is git-ignored. A failed call (any status but 200) is retried once a minute later and
both attempts are kept. A rate-limited call was not executed, so it is sent again, a minute apart, until it is answered
(up to an hour); if the limit never lifts it is recorded as rate-limited, the run is not complete, and a
resumed run sends it again. A resumed run skips companies that
already have an answer on disk and calls the failed and rate-limited ones again.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from company_enrichment.harness import providers

RUNS = Path(__file__).resolve().parents[1] / "runs"
VERSION = "1"
RETRY_WAIT_S = 60   # a failed call is retried once, a minute later
RATE_LIMIT_ROUNDS = 60   # a rate-limited call is re-sent until answered: up to an hour of rounds, RETRY_WAIT_S apart


def _write_json(path, value):
    """Replace a checkpoint atomically so interruption cannot truncate an answer."""
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=1))
    temporary.replace(path)


def unanswered(status, error):
    """A call without an answer: a transport failure, or a rate limit (the call was not executed)."""
    return status != 200 or providers.rate_limited(error)


def run_dir(label, out_root=RUNS, resume=False):
    out_root = Path(out_root)
    if resume:
        prior = sorted(p for p in out_root.glob(f"*-{label}") if (p / "meta.json").exists())
        if prior: return prior[-1]
    return out_root / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{label}"


def run(provider, rows, label=None, out_root=RUNS, limit=None, workers=4, resume=False, dataset=None, extra_meta=None):
    label = label or provider
    d = run_dir(label, out_root, resume)
    (d / "records").mkdir(parents=True, exist_ok=True)
    meta = {"provider": provider, "label": label, "dataset": dataset, "harness_version": VERSION,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **(extra_meta or {})}
    if (d / "meta.json").exists(): meta = {**json.loads((d / "meta.json").read_text()), "resumed_at": meta["started_at"]}
    _write_json(d / "meta.json", meta)
    rows = rows[:limit] if limit else rows
    on_disk = {p.stem: json.loads(p.read_text()) for p in (d / "records").glob("*.json")}
    done = {cid for cid, rec in on_disk.items() if not unanswered(rec.get("http_status"), rec.get("error"))}  # the rest are called again
    todo = [r for r in rows if r["id"] not in done]
    print(f"{provider}: {len(todo)} of {len(rows)} companies to call -> {d}", flush=True)
    by_domain = {}
    for r in todo: by_domain.setdefault(r["input"]["domain"], []).append(r["id"])

    def save(dom, res, first=None, attempts=1):
        for cid in by_domain[dom]:
            rec = {"company_id": cid, "provider": provider, "domain": dom, "http_status": res.http_status,
                   "latency_ms": res.latency_ms, "cost": res.cost, "error": res.error, "route": res.route,
                   "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "response": res.response}
            if first is not None: rec["first_attempt"], rec["attempts"] = first, attempts   # README "Calling providers"
            _write_json(d / "records" / f"{cid}.json", rec)
            err = f" ERR {str(res.error)[:60]}" if res.error else ""
            print(f"  {cid:12} {dom:30} {res.http_status} {'' if res.latency_ms is None else str(res.latency_ms) + 'ms'}{err}", flush=True)

    def call_all(doms):
        if provider in providers.BATCH:
            size = providers.BATCH[provider]
            for i in range(0, len(doms), size):
                yield providers.call(provider, doms[i:i + size])
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = [ex.submit(providers.call, provider, [dom]) for dom in doms]
                for future in as_completed(futures):
                    yield future.result()

    first, latest, attempts = {}, {}, {}
    for results in call_all(list(by_domain)):
        for dom, res in results.items():
            first[dom] = latest[dom] = res
            attempts[dom] = 1
            save(dom, res)
    # Checkpoint failures too: a stopped run can resume them without losing the
    # completed answers from other domains. Retry policy and scores are unchanged.
    again = [dom for dom, res in first.items() if unanswered(res.http_status, res.error)]
    while again:
        print(f"{provider}: {len(again)} calls without an answer, sending again in {RETRY_WAIT_S}s", flush=True)
        time.sleep(RETRY_WAIT_S)
        for results in call_all(again):
            for dom, res in results.items():
                latest[dom], attempts[dom] = res, attempts[dom] + 1
                initial = first[dom]
                save(dom, res, {"http_status": initial.http_status, "error": initial.error,
                                "latency_ms": initial.latency_ms}, attempts[dom])
        again = [dom for dom in again if providers.rate_limited(latest[dom].error) and attempts[dom] <= RATE_LIMIT_ROUNDS]
    return d
