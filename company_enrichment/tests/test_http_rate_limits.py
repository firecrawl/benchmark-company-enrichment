"""HTTP status is authoritative even when a rate-limit error has no special code."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from company_enrichment.harness import runner


@pytest.mark.parametrize("error", [{"error": "Too many requests"}, {}, "Slow down", None])
def test_http_429_recovers_after_more_than_one_retry(monkeypatch, tmp_path, error):
    record, calls = run_fixture(monkeypatch, tmp_path, [(429, error), (429, error), (200, {"name": "Fixture"})])
    assert record["http_status"] == 200
    assert record["response"] == {"name": "Fixture"}
    assert record["attempts"] == 3
    assert record["first_attempt"]["http_status"] == 429
    assert len(calls) == 3


def test_other_http_failures_still_retry_only_once(monkeypatch, tmp_path):
    record, calls = run_fixture(monkeypatch, tmp_path, [(503, {"error": "Unavailable"})] * 3)
    assert record["http_status"] == 503
    assert record["attempts"] == 2
    assert len(calls) == 2


def test_persistent_http_429_stops_at_the_existing_round_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "RATE_LIMIT_ROUNDS", 2)
    record, calls = run_fixture(monkeypatch, tmp_path, [(429, {"error": "Slow down"})] * 4)
    assert record["http_status"] == 429
    assert record["attempts"] == 3  # initial call plus two permitted retry rounds
    assert len(calls) == 3


def run_fixture(monkeypatch, tmp_path, replies):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            status, body = replies[min(len(calls) - 1, len(replies) - 1)]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("CE_EXT_URL", f"http://127.0.0.1:{server.server_port}/enrich")
    monkeypatch.delenv("CE_EXT_AUTH", raising=False)
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)  # shorten only the documented wait, not transport or retry logic
    try:
        saved = runner.run("external", [{"id": "C1", "input": {"domain": "fixture.example"}}], out_root=tmp_path)
        return json.loads((saved / "records/C1.json").read_text()), calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
