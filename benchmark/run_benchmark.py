"""run_benchmark.py: run the Company Enrichment benchmark against your own provider.

ONE PATH, ONE SCORER. Your endpoint is called exactly like every provider in the published table: the company's
website domain is the only input, one lookup per company, and a failed or empty answer counts as 0. Scores come from
company_enrichment.scorer.report_metrics, the module that produced the published numbers, so there is no second
implementation that can drift from it.

What your endpoint needs: one company per request, the domain either in the URL ({domain}) or as a JSON POST body
{"domain": "..."}, and an answer in the benchmark schema (README "Run your own provider"). Wrap your API in a small
shim if its own fields are named differently; the shim must not look anything up anywhere else.

Usage:
    python3 benchmark/run_benchmark.py --name yourco --endpoint 'https://api.yourco.com/enrich?domain={domain}' \\
        --auth "Bearer $KEY" --limit 10              # smoke
    python3 benchmark/run_benchmark.py --name yourco --endpoint ... --auth ...   # the whole public sample
    python3 benchmark/run_benchmark.py --name yourco --report-only --out results.json
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from company_enrichment.harness import runner  # noqa: E402
from company_enrichment.run_eval import load_env  # noqa: E402
from company_enrichment.scorer import dataset  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="label for your provider, e.g. 'yourco'")
    ap.add_argument("--endpoint", help="your URL: GET with {domain} in it, or POST {\"domain\": ...}")
    ap.add_argument("--auth", help="Authorization header, e.g. 'Bearer <key>'")
    ap.add_argument("--limit", type=int, help="first N companies only, for a smoke run")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true", help="continue your newest run; finished companies are skipped")
    ap.add_argument("--report-only", action="store_true", help="skip calling; re-score the run already on disk")
    ap.add_argument("--out", help="write your results JSON here")
    a = ap.parse_args()
    load_env()
    if not a.report_only:
        if not (a.endpoint or os.environ.get("CE_EXT_URL")): sys.exit("give --endpoint: your provider, called like every published one")
        if a.endpoint: os.environ["CE_EXT_URL"] = a.endpoint
        if a.auth: os.environ["CE_EXT_AUTH"] = a.auth
        runner.run("external", dataset.load(), label=a.name, limit=a.limit, workers=a.workers, resume=a.resume,
                   dataset=dataset.DEFAULT)
    sys.argv = [sys.argv[0], "--label", a.name] + (["--out", a.out] if a.out else [])
    from company_enrichment.scorer import report_metrics
    return report_metrics.main()


if __name__ == "__main__":
    sys.exit(main())
