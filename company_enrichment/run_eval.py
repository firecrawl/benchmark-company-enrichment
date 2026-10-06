#!/usr/bin/env python3
"""Run one provider over the answer key's companies and save every raw answer.

    company-enrichment --provider apollo --limit 10          # smoke test
    company-enrichment --provider apollo                     # the public sample
    company-enrichment-report                                # score every run under company_enrichment/runs/

Credentials come from .env (see .env.example). Every call costs the provider's own credits; run --limit first.
Preflight runs first unless --skip-preflight: three probe lookups that check the provider still returns every field the
benchmark reads (harness/fields_manifest.json). If a provider renamed a field, this fails loudly instead of the
provider silently scoring 0 on it.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from company_enrichment.harness import preflight, providers, runner  # noqa: E402
from company_enrichment.scorer import dataset  # noqa: E402


def load_env():
    f = ROOT.parent / ".env"
    if not f.exists(): return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, choices=sorted(providers.ROUTES))
    ap.add_argument("--dataset", default=dataset.DEFAULT, help="file stem under company_enrichment/gt/ (default: the public sample)")
    ap.add_argument("--label", help="run directory suffix (default: the provider id; use your company name for --provider external)")
    ap.add_argument("--limit", type=int, help="first N companies only, for a smoke run")
    ap.add_argument("--workers", type=int, default=4, help="concurrent calls for one-domain-per-call providers")
    ap.add_argument("--output-dir", default=str(runner.RUNS))
    ap.add_argument("--resume", action="store_true", help="continue the newest run with the same label; finished companies are skipped")
    ap.add_argument("--endpoint", help="--provider external: your URL (GET with {domain} in it, or POST {\"domain\": ...})")
    ap.add_argument("--auth", help="--provider external: Authorization header value, e.g. 'Bearer <key>'")
    ap.add_argument("--skip-preflight", action="store_true", help="skip the field check (not for a published run)")
    a = ap.parse_args()
    load_env()
    if a.endpoint: os.environ["CE_EXT_URL"] = a.endpoint
    if a.auth: os.environ["CE_EXT_AUTH"] = a.auth
    rows = dataset.load(a.dataset)
    rep = None if a.skip_preflight else preflight.run(a.provider, runs=a.output_dir)
    d = runner.run(a.provider, rows, label=a.label, out_root=a.output_dir, limit=a.limit, workers=a.workers,
                   resume=a.resume, dataset=dataset.path(a.dataset).stem, extra_meta={"preflight": rep})
    print(f"\nsaved to {d}\nscore it: company-enrichment-report --runs {Path(a.output_dir)} --dataset {a.dataset}")


if __name__ == "__main__":
    main()
