"""One provider response -> the common record the scorer grades.

Each provider names its fields differently. This module is the only place that reads a provider's own field names, and
it follows each provider's public API documentation. Nothing is inferred from other fields: a value the provider did not
return stays empty and scores 0.

Common record keys: name, domain, linkedin_url, li_slug, li_ids (set of numeric page IDs), city, state, country,
emp_count, emp_band, industry, founded (year), tickers (list), revenue.
"""
import json
import re

from company_enrichment.scorer.normalize import (band_of_label, is_us, li_id, li_parse, li_slug, parse_range,
                                                 tick_norm, to_year)

PROVIDERS = ("apollo", "fullenrich", "datalegion", "pdl", "ocean", "contactout", "external")


def _json(x):
    if isinstance(x, str):
        try: return json.loads(x)
        except ValueError: return None
    return x


def _metro_label(city):
    """LinkedIn-style metro labels ("Greater Chicago Area") read as the city."""
    return re.sub(r"^greater\s+|\s+(metropolitan\s+)?area$", "", city, flags=re.I).strip() if city else city


def _base(provider, r):
    """Provider response -> (name, domain, linkedin id, linkedin url, linkedin slug, city, state, industry, founded,
    tickers, revenue), or None when no company came back."""
    if provider == "apollo":
        o = r.get("organization")
        if not o: return None
        tk = o.get("publicly_traded_symbol")
        return dict(name=o.get("name"), domain=o.get("primary_domain") or o.get("website_url"),
                    linkedin=li_id(o.get("linkedin_uid")), linkedin_url=o.get("linkedin_url"), city=o.get("city"),
                    state=o.get("state"), industry=o.get("industry"), founded=o.get("founded_year"),
                    tickers=[tk] if tk else [], revenue=o.get("annual_revenue"))
    if provider == "fullenrich":
        cs = r.get("companies") or []
        if not cs: return None
        c = cs[0]; hq = (c.get("locations") or {}).get("headquarters") or {}
        pn = (c.get("social_profiles") or {}).get("professional_network") or {}
        return dict(name=c.get("name"), domain=c.get("domain"), linkedin=li_id(pn.get("id")), linkedin_url=pn.get("url"),
                    city=hq.get("city"), state=hq.get("region"), industry=(c.get("industry") or {}).get("main_industry"),
                    founded=c.get("year_founded") or None, tickers=[], revenue=None)
    if provider == "datalegion":
        ms = r.get("matches") or []
        if not ms: return None
        c = ms[0].get("company") or {}
        name = c.get("name") or {}
        return dict(name=(name.get("display") or name.get("cleaned")) if isinstance(name, dict) else name,
                    domain=c.get("domain"), linkedin=li_id(c.get("linkedin_id")), linkedin_url=c.get("linkedin_url"),
                    city=None, state=None, industry=c.get("industry"), founded=c.get("founded"),
                    tickers=[x.get("symbol") for x in c.get("tickers") or [] if x.get("symbol")], revenue=None)
    if provider == "pdl":
        loc = r.get("location") or {}
        return dict(name=r.get("display_name") or r.get("name"), domain=r.get("website"), linkedin=li_id(r.get("linkedin_id")),
                    linkedin_url=r.get("linkedin_url"), linkedin_slug=r.get("linkedin_slug") or li_slug(r.get("linkedin_url")),
                    city=_metro_label(loc.get("locality")), state=loc.get("region"), industry=r.get("industry"),
                    founded=r.get("founded"), tickers=[r["ticker"]] if r.get("ticker") else [], revenue=None)
    if provider == "ocean":
        locs = r.get("locations") or []
        hq = next((l for l in locs if l.get("primary")), locs[0] if locs else {})
        li = ((r.get("medias") or {}).get("linkedin") or {}).get("url")
        return dict(name=r.get("name"), domain=r.get("domain") or r.get("rootUrl"), linkedin=None, linkedin_url=li,
                    linkedin_slug=li_slug(li), city=_metro_label(hq.get("locality")), state=hq.get("region"),
                    industry=r.get("linkedinIndustry") or ((r.get("industries") or [None])[0]), founded=r.get("yearFounded"),
                    tickers=[], revenue=None)
    if provider == "contactout":
        # HQ is one free-text address ("street, city, ST, zip, CC"); an address without a city counts as no HQ city
        parts = [x.strip() for x in str(r.get("headquarter") or "").split(",") if x.strip()]
        country = parts.pop() if parts and re.fullmatch(r"[A-Z]{2}", parts[-1]) else None
        postcode = bool(parts and any(ch.isdigit() for ch in parts[-1]) and re.fullmatch(r"[\dA-Z -]{3,10}", parts[-1]))
        if postcode: parts.pop()
        # a street part has a number, starts with or numbers a suite / floor word, or ends with a street type; a city may
        # start with one ('St Louis', 'St. Paul', 'Center Valley', 'Ave Maria') and may carry its state code ('Cape Coral
        # FL', final-run audit X1: 'FL' was read as a floor word)
        streetish = lambda x: bool(re.search(r"\d|^(suite|ste|floor|fl)\b|\b(suite|ste|floor|fl)\s*#|\bplaza\b|\b(avenue|ave|street|st|road|"
                                             r"rd|blvd|drive|dr|way|place|center|parkway|pkwy|lane|ln|court|ct)\.?$", x.strip(), re.I))
        unstate = lambda x: re.sub(r"\s+[A-Z]{2}$", "", x.strip()) if x else x  # noqa: E731
        city = state = None
        if parts and is_us(parts[-1]):
            state = parts[-1]; city = unstate(parts[-2]) if len(parts) >= 2 and not streetish(parts[-2]) else None
        elif country and country != "US" and parts and not streetish(parts[-1]):
            # the segment before the region is the city; a business-park or estate line is never one (final-run audit X2:
            # 'Gloucester Business Park, Gloucester, GL3 4FE' gave the business park)
            park = lambda x: bool(re.search(r"\b(business|industrial|science|technology|office|retail) park\b|\bestate\b", x, re.I))  # noqa: E731
            city = parts[-2] if len(parts) >= 2 and not streetish(parts[-2]) and not park(parts[-2]) else parts[-1]
        li = r.get("li_vanity")
        m = re.fullmatch(r"\$?\s*([\d.]+)\s*([KMBT]?)", str(r.get("revenue") or "").strip(), re.I)
        rev = float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2).upper()] if m else None
        return dict(name=r.get("name"), domain=r.get("domain") or r.get("website"), linkedin=None, linkedin_url=li,
                    linkedin_slug=li_slug(li), city=_metro_label(city), state=state, industry=r.get("industry"),
                    founded=r.get("founded_at"), tickers=[], revenue=rev)
    if provider == "external":  # the benchmark's own schema (README "Run your own provider")
        country = (r.get("hq_country") or "").strip()
        us = country.lower() in ("", "us", "usa", "united states", "united states of america")
        tk = (r.get("stock_ticker") or "").split(":")[-1].strip()
        li = r.get("linkedin_url")
        return dict(name=r.get("company_name") or r.get("name"), domain=r.get("website") or r.get("domain"),
                    linkedin=li_id(r.get("linkedin_id")), linkedin_url=li, linkedin_slug=li_slug(li),
                    city=_metro_label(r.get("hq_city")), state=r.get("hq_state") if us else (r.get("hq_state") or country),
                    industry=r.get("industry"), founded=r.get("founded_year"), tickers=[tk] if tk else [],
                    revenue=r.get("annual_revenue_usd") if isinstance(r.get("annual_revenue_usd"), (int, float)) else None)
    raise ValueError(f"unknown provider {provider!r}")


def record(provider, resp):
    """Raw response -> common record, or None when the provider returned no company."""
    resp = _json(resp)
    if not resp: return None
    if provider == "pdl" and isinstance(resp, dict) and "data" in resp and "id" not in resp: resp = resp["data"]
    p = _base(provider, resp)
    if not p: return None
    R = {"name": p.get("name"), "domain": p.get("domain"), "linkedin_url": p.get("linkedin_url"),
         "li_ids": {str(x) for x in [p.get("linkedin")] if x},  # the record's main page only, not lists of linked pages
         "city": p.get("city"), "state": p.get("state"), "country": None, "emp_count": None, "emp_band": None,
         "industry": (p.get("industry") or "").strip() or None, "founded": to_year(p.get("founded")),
         "tickers": [t for t in (p.get("tickers") or []) if t], "revenue": p.get("revenue")}
    if provider == "apollo":
        o = resp["organization"]; R.update(country=o.get("country"), emp_count=o.get("estimated_num_employees"))
        if not R["city"] and o.get("raw_address"):  # "117 Adams St, Brooklyn, NY 11201"
            m = re.search(r",\s*([A-Za-z .'-]+),\s*([A-Z]{2})\s+\d{5}", o["raw_address"])
            if m: R.update(city=m.group(1).strip(), state=m.group(2))
            else:  # non-US: "51 Lime Street, London, England EC3M 7DQ, GB" (final-run audit X4)
                m = re.search(r",\s*([A-Za-z .'-]+?)\s*,\s*[A-Za-z ]*?\s*[A-Z0-9]{2,4}\s?[A-Z0-9]{3}\s*,\s*[A-Z]{2}\s*$", o["raw_address"])
                if m: R.update(city=m.group(1).strip())
    elif provider == "fullenrich":
        c = resp["companies"][0]; hq = (c.get("locations") or {}).get("headquarters") or {}
        R.update(country=hq.get("country_code") or hq.get("country"), emp_count=c.get("headcount"),
                 emp_band=c.get("headcount_range"))
        if not R["city"] and hq.get("line2"):  # "500 Charles Ewing Boulevard Ewing, New Jersey 08628"
            m = re.search(r"(?:^|\b(?:boulevard|blvd|drive|dr|street|st|avenue|ave|road|rd|way|lane|ln|parkway|pkwy|court|ct|"
                          r"place|pl|suite\s+\S+)\s+)([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)?),\s*([A-Za-z ]+?)\s+\d{5}", hq["line2"], re.I)
            if m: R.update(city=m.group(1).strip(), state=m.group(2).strip())
            else:  # "Cape Coral FL, Florida 33904, US"; non-US "Gloucester , GL3 4FE, GB" (final-run audit X3)
                m = re.search(r"^\s*([A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,2}?)(?:\s+[A-Z]{2})?\s*,\s*([A-Za-z ]+?)\s+\d{5}", hq["line2"]) \
                    or re.search(r"^\s*([A-Z][A-Za-z .'-]+?)\s*,\s*[A-Z0-9]{2,4}\s?[A-Z0-9]{3}\s*,\s*[A-Z]{2}\s*$", hq["line2"])
                if m: R.update(city=m.group(1).strip(), state=(m.group(2).strip() if m.lastindex and m.lastindex >= 2 else R["state"]))
    elif provider == "datalegion":
        c = resp["matches"][0].get("company") or {}
        R.update(emp_count=c.get("legion_employee_count"), emp_band=c.get("size"))
    elif provider == "pdl":
        R.update(country=(resp.get("location") or {}).get("country"), emp_count=resp.get("employee_count"),
                 emp_band=resp.get("size"), revenue=resp.get("inferred_revenue"))
        if not R["industry"] and resp.get("industry_v2"): R["industry"] = str(resp["industry_v2"]).strip() or None
    elif provider == "ocean":
        locs = resp.get("locations") or []
        hq = next((l for l in locs if l.get("primary")), locs[0] if locs else {})
        R.update(country=hq.get("country") or resp.get("primaryCountry"),
                 emp_count=resp.get("employeeCountOcean") or resp.get("employeeCountLinkedin"),
                 emp_band=resp.get("companySize"), revenue=resp.get("revenue"))
    elif provider == "contactout":
        cc = str(resp.get("headquarter") or "").split(",")[-1].strip().upper()
        if not R["city"]:  # "8665 E. Hartford Drive, Suite 200 Scottsdale, Arizona 85255, US"
            m = re.search(r"suite\s+\S+\s+([A-Za-z .'-]+?),\s*([A-Za-z ]+?)\s+\d{5}", str(resp.get("headquarter") or ""), re.I)
            if m: R.update(city=m.group(1).strip(), state=m.group(2).strip())
        R.update(country=resp.get("country") or (cc if re.fullmatch(r"[A-Z]{2}", cc) else None),
                 emp_count=resp.get("employees"),
                 emp_band=band_of_label(str(resp["size"])) if resp.get("size") else None, revenue=resp.get("revenue"))
    elif provider == "external":
        R.update(state=resp.get("hq_state"), country=resp.get("hq_country"), emp_count=resp.get("employee_count"),
                 emp_band=resp.get("employee_range"), revenue=resp.get("annual_revenue_usd"))
    try: R["emp_count"] = int(float(R["emp_count"])) if R["emp_count"] not in (None, "") and float(R["emp_count"]) > 0 else None
    except (TypeError, ValueError): R["emp_count"] = None
    slug, lid = li_parse(R["linkedin_url"])
    R["li_slug"] = slug or (str(p.get("linkedin_slug")).lower() if p.get("linkedin_slug") else None)
    if lid: R["li_ids"].add(lid)
    return R


def answered(R, f):
    """Did the record return a value for field f?"""
    if R is None: return False
    return {"hq": bool(R["city"]), "industry": bool(R["industry"]),
            "employees": bool(R["emp_count"] or parse_range(R["emp_band"])), "founded": R["founded"] is not None,
            "linkedin": bool(R["li_slug"] or R["li_ids"]), "ticker": any(tick_norm(t) for t in R["tickers"]),
            "revenue": parse_range(R["revenue"]) is not None}[f]
