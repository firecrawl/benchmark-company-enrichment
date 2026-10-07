"""Provider adapters: every provider gets the company's website domain and nothing else, in one lookup per company.

  apollo, fullenrich, datalegion        Firecrawl Alexandria (POST /v2/scrape with an `alexandria` call), FIRECRAWL_API_KEY
  pdl                                   People Data Labs GET /v5/company/enrich?website=, PDL_API_KEY
  ocean                                 Ocean.io POST /v2/lookup/companies (up to 1,000 domains a call), OCEAN_API_KEY
  contactout                            ContactOut POST /v1/domain/enrich (up to 30 domains a call), CONTACTOUT_API_KEY
  external                              your own HTTP endpoint returning the benchmark schema (README "Run your own provider")

The full response is saved as the provider sent it; nothing is trimmed at call time, and records.py reads the fields.
A failed call is recorded with its status and counts as an empty answer: it stays in the denominator. A rate-limited
call was not executed, so it is not an answer: it is sent again (here with a short backoff, and by runner.py).
Each adapter takes a list of domains and returns {domain: Result}. ROUTES maps a provider to its adapter, so a
different transport can be plugged in without touching the scorer.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

SYSTEMS = {  # provider id -> how it is shown in results; tag "alexandria" = available through Firecrawl Alexandria
    "apollo": {"name": "Apollo.io", "tag": "alexandria", "via": "Firecrawl Alexandria"},
    "fullenrich": {"name": "FullEnrich", "tag": "alexandria", "via": "Firecrawl Alexandria"},
    "datalegion": {"name": "Data Legion", "tag": "alexandria", "via": "Firecrawl Alexandria (Premium record)"},
    "pdl": {"name": "People Data Labs", "tag": "alexandria", "via": "Firecrawl Alexandria"},
    "ocean": {"name": "Ocean.io", "tag": None, "via": "Ocean.io API"},
    "contactout": {"name": "ContactOut", "tag": None, "via": "ContactOut API"},
}
ALEXANDRIA = {  # provider id -> (Alexandria provider, capability, option that carries the domain)
    "apollo": ("apollo", "companies/enrich", "domain"),
    "fullenrich": ("fullenrich", "companies/lookup", "domain"),
    "datalegion": ("datalegion", "companies/enrich-premium", "domain"),
}
KEYS = {"apollo": "FIRECRAWL_API_KEY", "fullenrich": "FIRECRAWL_API_KEY", "datalegion": "FIRECRAWL_API_KEY",
        "pdl": "PDL_API_KEY", "ocean": "OCEAN_API_KEY", "contactout": "CONTACTOUT_API_KEY"}
BATCH = {"ocean": 1000, "contactout": 30}   # domains per call; the others take one


RATE_LIMITED = {"rate_limited", "provider_rate_limited"}   # Alexandria's own limit, and the data source's limit behind it


def rate_limited(error):
    """True when an answer says the call was rate-limited (not executed) rather than answered."""
    return isinstance(error, dict) and (error.get("code") in RATE_LIMITED or error.get("status") == 429)


@dataclass
class Result:
    http_status: int
    response: object = None
    latency_ms: int = None
    cost: dict = field(default_factory=dict)
    error: object = None
    route: str = "direct"


def _post(url, body, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, out = r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        status = e.code
        try: out = json.loads(e.read())
        except Exception: out = {"error": str(e)}
    except Exception as e:
        status, out = 0, {"error": repr(e)}
    return status, out, round((time.perf_counter() - t) * 1000)


def _get(url, headers, timeout=120):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, out = r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        status = e.code
        try: out = json.loads(e.read())
        except Exception: out = {"error": str(e)}
    except Exception as e:
        status, out = 0, {"error": repr(e)}
    return status, out, round((time.perf_counter() - t) * 1000)


def _key(provider):
    k = os.environ.get(KEYS[provider])
    if not k: raise SystemExit(f"{provider} needs {KEYS[provider]} (see .env.example)")
    return k


def alexandria(provider, domains):
    """One /v2/scrape request per domain, so latency is per provider. A rate-limited answer (`rate_limited`, or
    `provider_rate_limited` from the data source) was not executed (0 credits) and is retried with backoff; if it is
    still rate-limited, runner.py sends it again later. Any other answer is final."""
    api = (os.environ.get("FIRECRAWL_API_URL") or "https://api.firecrawl.dev").rstrip("/")
    prov, cap, opt = ALEXANDRIA[provider]
    out = {}
    for d in domains:
        for attempt in range(6):
            status, body, ms = _post(f"{api}/v2/scrape", {"alexandria": [{"provider": prov, "capability": cap, "options": {opt: d}}]},
                                     {"Authorization": f"Bearer {_key(provider)}", "X-Request-ID": str(uuid.uuid4())})
            item = (((body or {}).get("data") or {}).get("alexandria") or [{}])[0] if isinstance(body, dict) else {}
            if rate_limited(item.get("error")) and attempt < 5: time.sleep(2 * (attempt + 1)); continue
            break
        err = item.get("error") or ((body or {}).get("error") if status != 200 and isinstance(body, dict) else None)
        out[d] = Result(status, item.get("data"), ms, {"credits": item.get("creditsCost") or 0}, err, "Firecrawl Alexandria")
    return out


def pdl(domains):
    """People Data Labs Company Enrichment API, one GET per domain with the domain as `website`. A domain PDL cannot
    match (HTTP 404) is recorded like on the other routes, as an answer with error not_found; a 429 was not executed
    and is retried with backoff (runner.py sends it again later if it persists)."""
    out = {}
    for d in domains:
        for attempt in range(6):
            status, body, ms = _get("https://api.peopledatalabs.com/v5/company/enrich?" + urllib.parse.urlencode({"website": d}),
                                    {"X-Api-Key": _key("pdl")})
            if status == 429 and attempt < 5: time.sleep(2 * (attempt + 1)); continue
            break
        if status == 404:
            out[d] = Result(200, None, ms, {"pdl_matches": 0}, {"code": "not_found", "status": 404}, "People Data Labs API")
        elif status == 200:
            out[d] = Result(200, body, ms, {"pdl_matches": 1}, None, "People Data Labs API")
        else:
            err = {"status": status, **(body if isinstance(body, dict) else {"error": body})}
            out[d] = Result(status, None, ms, {}, err, "People Data Labs API")
    return out


def ocean(domains):
    """Batch lookup. A domain Ocean lacks comes back as company null (an empty answer); redirectMap names the canonical
    domain it substituted, which is mapped back to the domain we sent."""
    out = {}
    for i in range(0, len(domains), BATCH["ocean"]):
        batch = domains[i:i + BATCH["ocean"]]
        status, body, ms = _post("https://api.ocean.io/v2/lookup/companies", {"domains": batch},
                                 {"X-Api-Token": _key("ocean")}, timeout=600)
        found = {}
        if status == 200 and isinstance(body, dict):
            redirect = {v: k for k, v in (body.get("redirectMap") or {}).items()}
            for c in body.get("companies") or []:
                comp = c.get("company") if "company" in c else c
                q = c.get("query") or c.get("domain") or (comp or {}).get("domain")
                found[redirect.get(q, q)] = comp
        for d in batch:
            out[d] = Result(status, found.get(d), ms if len(batch) == 1 else None,
                            {"ocean_results": 1 if found.get(d) else 0}, None if status == 200 else body, "Ocean.io API (batch)")
    return out


def contactout(domains):
    out = {}
    for i in range(0, len(domains), BATCH["contactout"]):
        batch = domains[i:i + BATCH["contactout"]]
        status, body, ms = _post("https://api.contactout.com/v1/domain/enrich", {"domains": batch}, {"token": _key("contactout")})
        found = {}
        if status == 200 and isinstance(body, dict):
            cs = body.get("companies") or {}
            for c in (cs if isinstance(cs, list) else [cs]):
                for dom, comp in c.items(): found[dom] = comp
        for d in batch:
            out[d] = Result(status, found.get(d), ms if len(batch) == 1 else None,
                            {"contactout_records": 1 if found.get(d) else 0}, None if status == 200 else body, "ContactOut API (batch)")
    return out


def external(domains):
    """Your endpoint: GET with {domain} in the URL, or POST {"domain": ...}; it returns the benchmark schema."""
    url = os.environ.get("CE_EXT_URL")
    if not url: raise SystemExit("external needs --endpoint (or CE_EXT_URL)")
    auth = {"Authorization": os.environ["CE_EXT_AUTH"]} if os.environ.get("CE_EXT_AUTH") else {}
    out = {}
    for d in domains:
        if "{domain}" in url:
            status, body, ms = _get(url.replace("{domain}", d), auth)
        else:
            status, body, ms = _post(url, {"domain": d}, auth)
        out[d] = Result(status, body if status == 200 else None, ms, {}, None if status == 200 else body, "external endpoint")
    return out


ROUTES = {"apollo": lambda ds: alexandria("apollo", ds), "fullenrich": lambda ds: alexandria("fullenrich", ds),
          "datalegion": lambda ds: alexandria("datalegion", ds), "pdl": pdl,
          "ocean": ocean, "contactout": contactout, "external": external}


def call(provider, domains):
    return ROUTES[provider](list(domains))
