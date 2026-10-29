"""Exercise the public Ocean adapter over actual local HTTP without provider credits."""
import json
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from company_enrichment.harness import providers


@pytest.mark.parametrize("redirects,companies,expected", [
    ({"a.example": "canonical.example", "b.example": "canonical.example"},
     [{"company": {"name": "Fixture", "domain": "canonical.example"}}],
     {"a.example": "Fixture", "b.example": "Fixture"}),
    ({"a.example": "canonical.example"},
     [{"query": "a.example", "company": {"name": "Direct", "domain": "canonical.example"}},
      {"domain": "b.example", "name": "Other"}],
     {"a.example": "Direct", "b.example": "Other"}),
    ({"a.example": "missing.example"}, [], {"a.example": None, "b.example": None}),
])
def test_ocean_redirects_keep_each_requested_alias(monkeypatch, redirects, companies, expected):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"redirectMap": redirects, "companies": companies}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    class FixtureRoute(urllib.request.HTTPSHandler):
        def https_open(self, request):
            assert urllib.parse.urlsplit(request.full_url).hostname == "api.ocean.io"
            local = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/v2/lookup/companies",
                data=request.data, headers=dict(request.header_items()), method=request.method,
            )
            return self.parent.open(local)

    monkeypatch.setenv("OCEAN_API_KEY", "local-fixture")
    monkeypatch.setattr(urllib.request, "_opener", urllib.request.build_opener(FixtureRoute()))
    try:
        results = providers.call("ocean", ["a.example", "b.example"])
        actual = {domain: result.response.get("name") if result.response else None for domain, result in results.items()}
        assert actual == expected
        assert calls == [{"domains": ["a.example", "b.example"]}]
        assert all(result.http_status == 200 for result in results.values())
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
