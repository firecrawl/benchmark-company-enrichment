"""Real HTTP responses must not become transport failures when JSON decoding fails."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from company_enrichment.harness import runner


@pytest.mark.parametrize("provider,method", [("external", "GET"), ("external", "POST"), ("apollo", "POST")])
@pytest.mark.parametrize("status,body,expected,attempts", [
    (200, "Upstream returned an HTML diagnostic, not JSON. 🚒", "Upstream returned an HTML diagnostic, not JSON. 🚒", 1),
    (502, "Fixture upstream unavailable", None, 2),
    (500, "Partial error body", None, 2),
    (200, '{"name":"Fixture"}', {"name": "Fixture"}, 1),
])
def test_http_status_and_diagnostic_body_survive_decoding(monkeypatch, tmp_path, provider, method, status, body, expected, attempts):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self):
            if self.command == "POST":
                self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.command)
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            if status == 500:
                self.send_header("Content-Length", str(len(body.encode()) + 10))
            self.end_headers()
            payload = body
            if provider == "apollo" and body.startswith("{"):
                payload = json.dumps({"data": {"alexandria": [{"data": json.loads(body)}]}})
            self.wfile.write(payload.encode())

        do_GET = do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/enrich"
    monkeypatch.setenv("CE_EXT_URL", endpoint + ("?domain={domain}" if method == "GET" else ""))
    monkeypatch.delenv("CE_EXT_AUTH", raising=False)
    monkeypatch.setenv("FIRECRAWL_API_URL", endpoint)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "local-fixture")
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    try:
        saved = runner.run(provider, [{"id": "C1", "input": {"domain": "fixture.example"}}], out_root=tmp_path)
        rec = json.loads((saved / "records/C1.json").read_text())
        assert rec["http_status"] == status
        assert rec["response"] == (None if provider == "apollo" and not body.startswith("{") else expected)
        assert calls == [method] * attempts
        if status == 502:
            assert rec["error"] == body
        if status == 500:
            diagnostic = rec["error"] if provider == "apollo" else rec["error"]["error"]
            assert diagnostic.startswith("HTTP Error 500")
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
