"""The external endpoint must receive the exact input domain as one URL value."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from company_enrichment.harness import providers


@pytest.mark.parametrize("domain", ["north-south.example", "xn--bcher-kva.example", "bücher.example", "a&b.example", "a/b?c.example"])
@pytest.mark.parametrize("template", ["/enrich?domain={domain}", "/enrich/{domain}"])
def test_external_get_domain_is_one_encoded_value(monkeypatch, domain, template):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            if template.startswith("/enrich?"):
                query = urllib.parse.parse_qs(parts.query)
                received = query.get("domain", [None])[0]
                seen.append(query)
            else:
                received = urllib.parse.unquote(parts.path[len("/enrich/"):])
                seen.append(parts.query)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"received": received}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("CE_EXT_URL", f"http://127.0.0.1:{server.server_port}" + template)
    monkeypatch.delenv("CE_EXT_AUTH", raising=False)
    try:
        result = providers.call("external", [domain])[domain]
        assert result.http_status == 200
        assert result.response == {"received": domain}
        assert seen == ([{"domain": [domain]}] if template.startswith("/enrich?") else [""])
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
