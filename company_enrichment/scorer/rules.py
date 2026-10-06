"""The scoring rules, one function per field, plus the wrong-company check (README "Method").

  hq         1 = the answer key's city, or another city in the same US metro area (Census CBSA); an accepted
             alternative location scores its own credit (0.5 unless the key marks it full credit); a country that
             differs from every accepted location is 0. Outside the US only the exact city counts.
  employees  1 within x1.2 of the key's interval [low, high], 0.5 within x2, else 0. Under 50 employees the x1.2
             tolerance widens to +/-5 when that is wider. The provider's own count is used when it returns one, else its
             band at the geometric midpoint (an open top band such as "10001+" at its lower bound).
  founded    1 within one year of the year the business started.
  linkedin   1 = the company's own LinkedIn page: the key's slug, another address of the same page, or its numeric ID.
  ticker     1 = any of the company's common-stock tickers (class punctuation ignored: BRK.B = BRK-B).
  revenue    1 within +/-5% of the latest fiscal year's revenue (USD). Revenue bands are scored at their midpoint.
  industry   1 = a best-fit label (either pre-registered annotator); 0.75 = a label in the right area but broader, or a
             secondary business; 0 = wrong. Labels outside the pre-registered lists are graded by five AI judges and
             the median counts (scorer/industry.py).
A record about a different company scores 0 on every field. An empty answer, an error or a field the provider does not
offer scores 0. A field the key has no value for is not graded for anyone.
"""
from company_enrichment.scorer.normalize import (is_us, li_parse, name_tokens, norm_city, norm_country, norm_state,
                                                 parse_range, point, reg_domain, tick_norm)

COMMON = ["hq", "employees", "founded", "linkedin"]   # the headline: fields scored by code alone, for every provider
# The all-fields score adds the extra fields: industry (AI judges grade labels outside the pre-registered lists, so it is
# kept out of the headline) and ticker and revenue (only some providers return them).
ALL = COMMON + ["industry", "ticker", "revenue"]


def key_has(K, f):
    return {"hq": K["hq"] is not None, "industry": K["industry"] is not None, "employees": K["employees"] is not None,
            "founded": K["founded"] is not None, "linkedin": K["linkedin"] is not None,
            "ticker": K["ticker"] is not None, "revenue": K["revenue"] is not None}[f]


def score_hq(R, K, M):
    """-> (score, note)."""
    pc = norm_country(R["country"]) or ("US" if R["state"] and is_us(R["state"]) else None)
    locs = [(K["hq"], 1.0)] + [(a, 1.0 if a["full_credit"] else 0.5) for a in K["hq_alts"]]
    best, note, country_ok = 0.0, "", False
    for l, cap in locs:
        if pc and l["country"] and pc != l["country"]: continue
        country_ok = True
        us = (l["country"] or pc) == "US"
        same = norm_city(R["city"]) == norm_city(l["city"]) and not (
            us and R["state"] and l["state"] and norm_state(R["state"]) != norm_state(l["state"]))
        if same:
            best = max(best, cap); continue
        if us and R["state"] and l["state"]:
            a, b = M.cbsa(R["city"], R["state"]), M.cbsa(l["city"], l["state"])
            if a not in (None, "unknown") and a == b:
                best = max(best, cap); note = note or f"same CBSA {a}"
            elif "unknown" in (a, b): note = "metro unknown"
    if not country_ok: note = "wrong country"
    return best, note


def emp_score(v, lo, hi, t1=1.2, t2=2.0):
    if lo <= v <= hi: return 1.0
    r, gap = (lo / max(v, 1e-9), lo - v) if v < lo else (v / hi, v - hi)
    if r <= t1 or (hi < 50 and gap <= 5): return 1.0
    return 0.5 if r <= t2 else 0.0


def emp_value(R):
    """-> (value scored, note)."""
    if R["emp_count"]: return float(R["emp_count"]), f"count {R['emp_count']}"
    rng = parse_range(R["emp_band"])
    return point(rng), f"band {R['emp_band']} -> {point(rng):.0f}" + (" (open band: lower bound)" if rng[1] is None else "")


def score_founded(R, K):
    return 1.0 if abs(R["founded"] - K["founded"]) <= 1 else 0.0


def score_linkedin(R, K):
    return 1.0 if (R["li_slug"] in K["linkedin"]["slugs"] or R["li_ids"] & K["linkedin"]["ids"]) else 0.0


def score_ticker(R, K):
    return 1.0 if {tick_norm(t) for t in R["tickers"]} & K["ticker"] else 0.0


def score_revenue(R, K):
    v, kv = point(parse_range(R["revenue"])), K["revenue"]["value"]
    return 1.0 if abs(v - kv) / kv <= .05 else 0.0


def wrong_company(R, K):
    """-> list of reasons the record describes another company (empty = the right company).

    A returned website that is not the input domain or one of the company's own domains makes a wrong company unless
    the record's identity is confirmed: its LinkedIn page is the company's, or, when the record or the key has no
    LinkedIn page, every word of the returned name is in one of the company's names. A record that matches the parent
    or another entity the key records (subsidiary, acquired company, sibling) by LinkedIn page, domain or name, or
    whose employee count is within 20% of the parent's, is also a wrong company."""
    if R is None: return []
    why = []
    rd = reg_domain(R["domain"]) if R["domain"] else None
    mine = set().union(*[name_tokens(n) for n in K["names"]]) if K["names"] else set()
    if rd and K["domains"] and rd not in K["domains"]:
        kli = K.get("linkedin") or {}
        li_match = bool((R["li_slug"] and R["li_slug"] in kli.get("slugs", set())) or (R["li_ids"] & kli.get("ids", set())))
        pt = name_tokens(R["name"])
        name_match = bool(pt) and any(pt <= name_tokens(n) for n in K["names"])
        no_li = not (R["li_slug"] or R["li_ids"]) or not kli
        if not (li_match or (name_match and no_li)):
            why.append(f"returned domain {rd} is not the input domain or an alias")
    for o in K["others"]:
        on = o.get("name") or "another entity"
        oslugs = {li_parse(x)[0] for x in [o.get("linkedin")] + list(o.get("linkedin_aliases") or [])} - {None}
        if R["li_slug"] and R["li_slug"] in oslugs: why.append(f"LinkedIn page of {on}")
        if rd and rd in {reg_domain(d) for d in [o.get("domain")] + list(o.get("domains") or []) if d}: why.append(f"domain of {on}")
        pt = name_tokens(R["name"])
        if pt and o.get("name") and pt & name_tokens(o["name"]) and not pt & mine: why.append(f"name matches {on}")
        pe = _num(o.get("employees"))
        if pe and R["emp_count"] and abs(R["emp_count"] - pe) <= 0.2 * pe and o is K["parent"]:
            why.append(f"employees within 20% of parent {on} ({pe:.0f})")
    return why


def _num(x):
    if isinstance(x, dict): x = x.get("value")
    try: return float(x) if x not in (None, "") else None
    except (TypeError, ValueError): return None
