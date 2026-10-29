"""Invalid concurrency must fail before credential probes or run-directory writes."""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from company_enrichment.harness import runner

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("workers", [0, -2])
def test_direct_runner_rejects_before_creating_artifacts(tmp_path, workers):
    output = tmp_path / "runs"
    with pytest.raises(ValueError, match="workers"):
        runner.run("external", [{"id": "C1", "input": {"domain": "fixture.example"}}], workers=workers, out_root=output)
    assert not output.exists()


@pytest.mark.parametrize("entry", ["provider", "benchmark"])
@pytest.mark.parametrize("workers", [0, -2])
def test_both_public_clis_reject_before_preflight_or_files(tmp_path, entry, workers):
    result, calls, output = run_cli_fixture(tmp_path, entry, workers)
    assert (result.returncode, calls, output.exists()) == (2, [], False), result.stderr
    assert "--workers" in result.stderr and "positive" in result.stderr


def test_valid_worker_count_still_calls_preflight_and_saves_answer(tmp_path):
    result, calls, output = run_cli_fixture(tmp_path, "provider", 1)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 4  # three real preflight probes and the requested one-company smoke run
    assert len(list(output.glob("*/records/*.json"))) == 1


def run_cli_fixture(tmp_path, entry, workers):
    # The external benchmark CLI has no --output-dir option. Run real copies of
    # the public source in a temporary project, never the contributor's runs/.
    project = tmp_path / "project"
    shutil.copytree(ROOT / "company_enrichment", project / "company_enrichment",
                    ignore=shutil.ignore_patterns("runs", "__pycache__"))
    shutil.copytree(ROOT / "benchmark", project / "benchmark", ignore=shutil.ignore_patterns("__pycache__"))
    calls = []
    organization = {"name": "Fixture", "primary_domain": "fixture.example", "linkedin_url": "https://linkedin.com/company/fixture",
                    "city": "Fixture City", "state": "CA", "country": "US", "estimated_num_employees": 42,
                    "industry": "Software", "founded_year": 2000, "publicly_traded_symbol": "FIX", "annual_revenue": 1000}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": {"alexandria": [{"data": {"organization": organization}}]}}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    output = project / "company_enrichment/runs" if entry == "benchmark" else tmp_path / "runs"
    args = (["-m", "company_enrichment.run_eval", "--provider", "apollo", "--output-dir", str(output)] if entry == "provider"
            else [str(project / "benchmark/run_benchmark.py"), "--name", "fixture", "--endpoint", endpoint])
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(project), "PYTHONDONTWRITEBYTECODE": "1",
           "FIRECRAWL_API_URL": endpoint, "FIRECRAWL_API_KEY": "local-fixture"}
    try:
        result = subprocess.run([sys.executable, *args, "--workers", str(workers), "--limit", "1"],
                                cwd=project, env=env, text=True, capture_output=True, timeout=20)
        return result, calls, output
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
