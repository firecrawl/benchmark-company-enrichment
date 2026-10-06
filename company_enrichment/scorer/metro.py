"""US metro areas (Census Core Based Statistical Areas) for the HQ rule: two cities in the same CBSA are the same HQ.

gt/hq_metro.json maps 'city|state' (normalize.loc_key) to a CBSA code, or null for a place outside every metro area. The
public table covers the public sample's answer-key locations and every US location a published provider returned for
those companies; maintainers also have the held-back gt/hq_metro_full_v<N>.json (git-ignored), read when present. A location not in the
table is 'unknown': only an exact city match can score it, and `company-enrichment-report --geocode` looks it up
(OpenStreetMap Nominatim for coordinates, then the US Census geocoder for the CBSA; free, about one request a second)
and writes the result to runs/hq_metro_local.json, which later reports read too.
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from company_enrichment.scorer.normalize import loc_key, norm_state

TABLE = Path(__file__).resolve().parents[1] / "gt" / "hq_metro.json"
UA = {"User-Agent": "company-enrichment-benchmark (scorer; HQ metro lookup; +https://github.com/firecrawl/benchmark-company-enrichment)"}


class Metro:
    def __init__(self, local=None):
        self.tab = json.loads(TABLE.read_text())
        for full in sorted(TABLE.parent.glob("hq_metro_full_v*.json")): self.tab.update(json.loads(full.read_text()))
        self.local = Path(local) if local else None
        if self.local and self.local.exists(): self.tab.update(json.loads(self.local.read_text()))
        self.pending = {}

    def cbsa(self, city, state):
        """CBSA code, None (known: no metro area) or 'unknown'."""
        k = loc_key(city, state)
        if k in self.tab: return self.tab[k]
        self.pending[k] = {"city": city, "state": state}
        return "unknown"

    def fetch_pending(self):
        if not self.local: raise ValueError("no local metro file to write")
        out = json.loads(self.local.read_text()) if self.local.exists() else {}
        get = lambda u: json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60).read())
        for k, v in self.pending.items():
            try:
                res = get("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" +
                          urllib.parse.quote(f"{v['city']}, {norm_state(v['state']).title()}, USA"))
                time.sleep(1.1)
                if not res: out[k] = None; continue
                lat, lon = float(res[0]["lat"]), float(res[0]["lon"])
                g = get("https://geocoding.geo.census.gov/geocoder/geographies/coordinates?benchmark=Public_AR_Current"
                        "&vintage=Current_Current&format=json&layers=Metropolitan%20Statistical%20Areas,Micropolitan%20Statistical%20Areas"
                        f"&x={lon}&y={lat}")["result"]["geographies"]
                hit = (g.get("Metropolitan Statistical Areas") or g.get("Micropolitan Statistical Areas") or [None])[0]
                out[k] = (hit or {}).get("CBSA") or (hit or {}).get("GEOID")
            except Exception as e:  # network trouble: the location stays unknown
                print(f"geocode {k}: {e!r}"[:160])
        self.local.parent.mkdir(parents=True, exist_ok=True)
        self.local.write_text(json.dumps(out, indent=1, sort_keys=True))
        return len(out)
