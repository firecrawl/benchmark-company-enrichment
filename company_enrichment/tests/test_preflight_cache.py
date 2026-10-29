"""Public preflight cache admission against a real, synthetic HTTP provider."""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from company_enrichment.harness import preflight


@pytest.fixture
def provider(monkeypatch):
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
            org = {"name": "Fixture"} if self.path.startswith("/changed/") else organization
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": {"alexandria": [{"data": {"organization": org}}]}}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("FIRECRAWL_API_URL", endpoint)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "local-fixture")
    try:
        yield endpoint, calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_same_contract_reuses_cache(provider, tmp_path):
    _, calls = provider
    first = preflight.run("apollo", runs=tmp_path)
    assert preflight.run("apollo", runs=tmp_path) == first
    assert len(calls) == 3


def test_endpoint_change_requires_new_preflight(provider, tmp_path, monkeypatch):
    endpoint, calls = provider
    preflight.run("apollo", runs=tmp_path)
    monkeypatch.setenv("FIRECRAWL_API_URL", endpoint + "/changed")
    with pytest.raises(SystemExit, match="preflight failed"):
        preflight.run("apollo", runs=tmp_path)
    assert len(calls) == 6


def test_manifest_change_requires_new_preflight(provider, tmp_path, monkeypatch):
    _, calls = provider
    preflight.run("apollo", runs=tmp_path)
    manifest = copy.deepcopy(preflight.MANIFEST)
    manifest["providers"]["apollo"]["required"].append("organization.new_field")
    monkeypatch.setattr(preflight, "MANIFEST", manifest)
    with pytest.raises(SystemExit, match="new_field"):
        preflight.run("apollo", runs=tmp_path)
    assert len(calls) == 6


@pytest.mark.parametrize("saved", ["{invalid", "[]", '{"provider":"pdl","ok":true}'])
def test_corrupt_or_wrong_identity_cache_is_rechecked(provider, tmp_path, saved):
    _, calls = provider
    (tmp_path / "preflight_apollo.json").write_text(saved)
    assert preflight.run("apollo", runs=tmp_path)["ok"]
    assert len(calls) == 3
