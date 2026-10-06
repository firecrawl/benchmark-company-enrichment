"""Normalisation shared by the harness and the scorer: cities, states, countries, domains, LinkedIn pages, tickers,
numbers and bands.

Every rule here is part of the published scoring. Change one and every number in the README changes with it, which is
why tests/test_scorer.py pins the behaviour with examples.
"""
import math
import re
import unicodedata
import urllib.parse

# ---------------------------------------------------------------- US states
STATES = {"AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
          "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
          "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
          "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts",
          "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
          "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
          "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
          "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
          "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
          "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming", "PR": "puerto rico"}


def is_us(state):
    s = (state or "").strip()
    return s.upper() in STATES or s.lower() in STATES.values()


def norm_state(s):
    s = (s or "").strip()
    return STATES.get(s.upper(), s.lower())


def loc_key(city, state):
    """Key of the metro-area table (gt/hq_metro.json): 'city|state', lower-cased, postal district dropped."""
    c = re.sub(r"\s+", " ", (city or "").lower().replace(".", "").replace("saint ", "st ").strip())
    return re.sub(r"\s+\d+$", "", c) + "|" + norm_state(state)


# ---------------------------------------------------------------- cities and countries
CITY_ALIASES = {"nyc": "new york", "new york city": "new york", "manhattan": "new york", "brooklyn": "new york",
                "queens": "new york", "bronx": "new york", "staten island": "new york", "ny": "new york",
                "washington dc": "washington", "washington d c": "washington", "dc": "washington",
                "district of columbia": "washington", "sf": "san francisco", "philly": "philadelphia"}
COUNTRIES = {"us": "US", "usa": "US", "u s": "US", "u s a": "US", "united states": "US", "united states of america": "US",
             "america": "US", "uk": "GB", "gb": "GB", "united kingdom": "GB", "england": "GB", "great britain": "GB",
             "scotland": "GB", "wales": "GB", "ireland": "IE", "republic of ireland": "IE", "canada": "CA",
             "germany": "DE", "deutschland": "DE", "france": "FR", "netherlands": "NL", "the netherlands": "NL",
             "switzerland": "CH", "india": "IN", "japan": "JP", "china": "CN", "israel": "IL", "australia": "AU",
             "singapore": "SG", "mexico": "MX", "brazil": "BR", "spain": "ES", "italy": "IT", "sweden": "SE",
             "bermuda": "BM", "luxembourg": "LU", "jersey": "JE", "cayman islands": "KY", "puerto rico": "PR"}


def ascii_lower(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()


def norm_city(s):
    """Accents, punctuation, 'Greater ... Area', Saint/St, Fort/Ft, Mount/Mt, Twp, postal districts and common
    aliases (NYC -> New York) are normalised away before two cities are compared."""
    s = ascii_lower(s).replace(".", "").replace("'", "").replace("’", "")
    s = re.sub(r"^(greater|city of)\s+", "", s)
    s = re.sub(r"\s+(metropolitan area|metro area|metro|area)$", "", s)
    s = re.sub(r"[-/,]", " ", s)
    s = re.sub(r"\bsaint\b", "st", s); s = re.sub(r"\bft\b", "fort", s); s = re.sub(r"\bmt\b", "mount", s)
    s = re.sub(r"\btwp\b", "township", s); s = re.sub(r"^mc\s+lean$", "mclean", s)
    s = re.sub(r"\s+\d+$", "", s); s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return CITY_ALIASES.get(s, s)


def norm_country(s):
    x = re.sub(r"[.]", " ", ascii_lower(s)); x = re.sub(r"\s+", " ", x).strip()
    if not x: return None
    return COUNTRIES.get(x, x.upper() if len(x) == 2 else x)


# ---------------------------------------------------------------- names
STOP = {"inc", "corp", "corporation", "co", "company", "group", "the", "holdings", "plc", "ltd", "llc", "lp", "trust",
        "services", "properties", "and", "of", "international", "global"}


def name_tokens(s):
    """Distinctive words of a company name: legal suffixes and filler words dropped; '(Acquired by ...)' removed."""
    s = re.sub(r"\(.*?\)", " ", s or "")
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 1}


# ---------------------------------------------------------------- domains
def host(d):
    d = re.sub(r"^[a-z]+://", "", str(d or "").strip().lower())
    d = d.split("/")[0].split("?")[0].split(":")[0].strip(".")
    return d[4:] if d.startswith("www.") else d


try:  # tldextract's bundled Public Suffix List snapshot (no network); pinned in pyproject.toml
    import tldextract
    _TLD = tldextract.TLDExtract(suffix_list_urls=())
except Exception:  # pragma: no cover - the fallback only matters for multi-part suffixes
    _TLD = None
_SUFFIX2 = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp", "co.in", "com.br",
            "com.mx", "co.za", "com.cn", "com.sg", "com.hk", "co.kr", "com.tw", "com.ar", "co.il"}


def reg_domain(d):
    """Registrable domain: 'https://www.shop.kbhome.com/x' -> 'kbhome.com'."""
    h = host(d)
    if not h or "." not in h: return h or None
    if _TLD:
        r = _TLD(h)
        return getattr(r, "top_domain_under_public_suffix", None) or getattr(r, "registered_domain", None) or h
    parts = h.split(".")
    return ".".join(parts[-3:]) if ".".join(parts[-2:]) in _SUFFIX2 and len(parts) >= 3 else ".".join(parts[-2:])


# ---------------------------------------------------------------- LinkedIn pages
def li_norm(slug):
    """One page, several spellings: '&' or '%26' as a hyphen, repeated hyphens collapsed, a trailing dot dropped."""
    return re.sub(r"-{2,}", "-", re.sub(r"&|%26", "-", slug)).rstrip(".")


def li_parse(u):
    """LinkedIn URL, slug or numeric page ID -> (slug, id). Sub-pages and country subdomains are dropped."""
    if u in (None, "", 0): return None, None
    s = urllib.parse.unquote(str(u)).strip().lower()
    if s.isdigit(): return None, s
    m = re.search(r"linkedin\.com/(?:company|company-beta)/([^/?#\s]+)", s)
    if not m:
        return (None, None) if "linkedin.com" in s or "/" in s else (li_norm(s.strip("/")), None)
    slug = m.group(1).strip()
    return (None, slug) if slug.isdigit() else (li_norm(slug), None)


def li_slug(url):
    m = re.search(r"linkedin\.com/company/([^/?#]+)", url or "")
    return m.group(1).lower() if m else None


def li_id(x):
    return str(x) if x not in (None, "", 0) else None


# ---------------------------------------------------------------- tickers
def tick_norm(s):
    """'NYSE: BRK/B', 'BRK.B' and 'brk-b' all -> 'BRKB'."""
    s = re.sub(r"\(.*?\)", " ", str(s or "").upper()).split(":")[-1]
    return re.sub(r"[^A-Z0-9]", "", s) or None


# ---------------------------------------------------------------- numbers, bands, years
_MULT = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


def parse_range(x):
    """Number or band text ('1001-5000', '10 - 50', '10,001+', '$1.4B', '500-1000M', '$10B+') -> (lo, hi);
    hi None for an open band; lo == hi for a single figure."""
    if x is None or isinstance(x, bool): return None
    if isinstance(x, (int, float)): return (float(x), float(x)) if x > 0 else None
    t = str(x).lower().replace("billion", "b").replace("million", "m").replace("thousand", "k").replace("bn", "b").replace("mm", "m")
    found = re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*([kmbt]?)(?![a-z0-9])", t)
    if not found: return None
    last = next((s for _, s in reversed(found) if s), "")
    vals = [float(n.replace(",", "")) * _MULT[s or last] for n, s in found]
    if "+" in t or ">" in t or re.search(r"\b(over|more than|above|at least)\b", t): return (vals[0], None)
    if re.search(r"\b(under|less than|below|fewer than|up to)\b|<", t): return (1.0, vals[0])
    if len(vals) >= 2: return tuple(sorted(vals[:2]))
    return (vals[0], vals[0]) if vals[0] > 0 else None


def point(rng):
    """Value a range is scored at: the figure, the geometric midpoint of a band, the lower bound of an open band."""
    lo, hi = rng
    if hi is None: return lo
    return lo if lo == hi else math.sqrt(max(lo, 1.0) * hi)


def to_year(x):
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str(x or ""))
    return int(m.group(1)) if m else None


BANDS = [(1, 10), (11, 50), (51, 200), (201, 500), (501, 1000), (1001, 5000), (5001, 10000), (10001, 10**9)]


def band_of_count(n):
    for lo, hi in BANDS:
        if lo <= n <= hi: return f"{lo}-{hi}" if hi < 10**9 else "10001+"


def band_of_label(s):
    if not s: return None
    nums = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", str(s))]
    if not nums: return None
    return band_of_count(nums[0]) if "+" in str(s) or len(nums) == 1 else band_of_count((nums[0] + nums[1]) // 2)


def norm_label(s):
    """Industry label as LinkedIn's taxonomy keys it (gt/linkedin_industries.json 'by_label')."""
    return re.sub(r"\s+", " ", str(s).lower().replace("&", "and").replace("/", " and ").replace(",", "")).strip()
