"""The scoring rules, pinned with examples. No credentials, no network.

A change that moves any of these is a change to the published metric: it must come with a new version of the
benchmark, never as a silent edit.
"""
import math

import pytest

from company_enrichment.scorer import industry, rules
from company_enrichment.scorer import report_metrics as rm
from company_enrichment.scorer.normalize import (li_parse, norm_city, norm_country, parse_range, point, reg_domain,
                                                 tick_norm)


class FakeMetro:
    def __init__(self, tab): self.tab, self.pending = tab, {}

    def cbsa(self, city, state):
        return self.tab.get((norm_city(city), state), "unknown")


def rec(**kw):
    R = {"name": "Acme", "domain": "acme.com", "linkedin_url": None, "li_slug": None, "li_ids": set(), "city": None,
         "state": None, "country": None, "emp_count": None, "emp_band": None, "industry": None, "founded": None,
         "tickers": [], "revenue": None}
    R.update(kw)
    return R


def key(**kw):
    K = {"id": "C1", "group": "us_public", "name": "Acme Inc", "names": ["Acme Inc"], "domains": {"acme.com"},
         "hq": {"city": "Austin", "state": "TX", "country": "US", "full_credit": False}, "hq_alts": [],
         "employees": (900.0, 1100.0), "founded": 1998, "linkedin": {"slugs": {"acme-co"}, "ids": {"123"}},
         "ticker": {"ACME"}, "revenue": {"value": 1e9, "prior": None},
         "industry": {"best_fit": {"4"}, "either_best_fit": {"4", "6"}, "broad_ok": {"96"}, "description": "Software."},
         "parent": None, "others": []}
    K.update(kw)
    return K


class TestNormalisation:
    def test_cities(self):
        assert norm_city("New York City") == norm_city("NYC") == "new york"
        assert norm_city("Saint Louis") == norm_city("St. Louis") and norm_city("The Dalles") == "dalles"
        assert norm_city("São Paulo") == "sao paulo" and norm_city("Greater Chicago Area") == "chicago"
        assert norm_city("Concord Twp") == "concord township" and norm_city("Mc Lean") == "mclean"

    def test_countries(self):
        assert norm_country("United States") == norm_country("us") == "US" and norm_country("Ireland") == "IE"

    def test_ranges(self):
        assert parse_range("10 - 50") == (10, 50) and parse_range("10,001+") == (10001, None)
        assert parse_range(">1000M") == (1e9, None) and parse_range("$1.4B") == (1.4e9, 1.4e9)
        assert parse_range("500-1000M") == (5e8, 1e9) and parse_range("$10B+") == (1e10, None)
        assert abs(point((1001, 5000)) - math.sqrt(1001 * 5000)) < 1e-6 and point((10001, None)) == 10001

    def test_tickers_and_pages(self):
        assert tick_norm("BRK.B") == tick_norm("brk-b") == tick_norm("NYSE: BRK/B") == "BRKB" and tick_norm("XNAS:VLY") == "VLY"
        assert li_parse("https://uk.linkedin.com/company/Kadant-Inc-/about/") == ("kadant-inc-", None)
        assert li_parse("linkedin.com/company/19268") == (None, "19268") and li_parse("valleybank") == ("valleybank", None)
        assert li_parse("https://www.linkedin.com/company/church-&-dwight-co---inc.")[0] == "church-dwight-co-inc"
        assert reg_domain("https://www.shop.kbhome.com/x") == "kbhome.com" and reg_domain("foo.co.uk") == "foo.co.uk"


class TestFieldRules:
    def test_hq_same_city_metro_alternative_country(self):
        M = FakeMetro({("round rock", "TX"): "12420", ("austin", "TX"): "12420", ("dallas", "TX"): "19100"})
        assert rules.score_hq(rec(city="Austin", state="Texas"), key(), M)[0] == 1.0
        assert rules.score_hq(rec(city="Round Rock", state="TX"), key(), M) == (1.0, "same CBSA 12420")
        assert rules.score_hq(rec(city="Dallas", state="TX"), key(), M)[0] == 0.0
        K = key(hq_alts=[{"city": "Dallas", "state": "TX", "country": "US", "full_credit": False}])
        assert rules.score_hq(rec(city="Dallas", state="TX"), K, M)[0] == 0.5
        assert rules.score_hq(rec(city="Austin", country="Canada"), key(), M) == (0.0, "wrong country")

    def test_employees_tolerance_and_small_companies(self):
        assert rules.emp_score(1300, 900, 1100) == 1.0 and rules.emp_score(1400, 900, 1100) == 0.5
        assert rules.emp_score(2300, 900, 1100) == 0.0 and rules.emp_score(500, 900, 1100) == 0.5
        assert rules.emp_score(29, 18, 24) == 1.0 and rules.emp_score(45, 27, 32) == 0.5   # +/-5 under 50 (29/24 > x1.2)
        assert rules.emp_value(rec(emp_band="1001-5000"))[0] == pytest.approx(math.sqrt(1001 * 5000))
        assert rules.emp_value(rec(emp_count=950, emp_band="10001+"))[0] == 950   # own count before the band

    def test_founded_within_one_year(self):
        assert rules.score_founded(rec(founded=1999), key()) == 1.0 and rules.score_founded(rec(founded=2000), key()) == 0.0

    def test_linkedin_slug_or_page_id(self):
        assert rules.score_linkedin(rec(li_slug="acme-co"), key()) == 1.0
        assert rules.score_linkedin(rec(li_slug="acme-old", li_ids={"123"}), key()) == 1.0
        assert rules.score_linkedin(rec(li_slug="acme-holdings"), key()) == 0.0

    def test_ticker_and_revenue(self):
        assert rules.score_ticker(rec(tickers=["NASDAQ:ACME"]), key()) == 1.0
        assert rules.score_revenue(rec(revenue=1.04e9), key()) == 1.0 and rules.score_revenue(rec(revenue=1.06e9), key()) == 0.0
        assert rules.score_revenue(rec(revenue="$1B"), key()) == 1.0


class TestIndustry:
    def test_pre_registered_lists(self):
        label = lambda i: industry.TAXONOMY["ids"][i]["v2"]
        ok = [i for i, v in industry.TAXONOMY["ids"].items() if v.get("v2") and industry.label_id(v["v2"]) == i]
        a, b, c = ok[:3]
        K = key(industry={"best_fit": {a}, "either_best_fit": {a, b}, "broad_ok": {c}, "description": "Software."})
        assert industry.score(rec(industry=label(a)), K, {}) == (1.0, "pre-registered")
        assert industry.score(rec(industry=label(b)), K, {}) == (None, "needs judges")   # not in a list: judges decide
        assert industry.score(rec(industry=label(b)), K, {f"C1||#{b}": 0.0})[0] == 1.0   # unless an annotator chose it
        assert industry.score(rec(industry=label(c)), K, {})[0] == 0.75   # broad or secondary: 0.75

    def test_judges_median_and_pending(self):
        K = key()
        assert industry.score(rec(industry="Rocket Science Widgets"), K, {}) == (None, "needs judges")
        g = {industry.pair_key("C1", "Rocket Science Widgets"): 0.5}
        assert industry.score(rec(industry="Rocket Science Widgets"), K, g) == (0.75, "judges")

    def test_median_grade(self):
        assert industry.median_grade([1, 1, 0.5, 0, None]) == 1.0 and industry.median_grade([0.5, 0, 0, 1, 1]) == 0.5
        assert industry.median_grade([None, None]) is None

    def test_votes_need_every_judge(self, tmp_path):
        f = tmp_path / "v.jsonl"
        f.write_text('{"company_id": "C1", "label": "x", "votes": {"opus": 1, "sonnet": 1}}\n')
        assert industry.load_grades([f]) == {}
        f.write_text('{"company_id": "C1", "label": "x", "votes": {"opus": 1, "sonnet": 0.5, "gemini": 0, "sol": 0.5, "astra": null}}\n')
        assert industry.load_grades([f]) == {"C1||x": 0.5}


class TestWrongCompany:
    def test_same_company_other_website_confirmed_by_linkedin(self):
        K = key(domains={"acme-corp.com"})
        assert rules.wrong_company(rec(li_slug="acme-co"), K) == []
        assert rules.wrong_company(rec(li_slug="acme-holdings"), K)

    def test_name_decides_without_a_linkedin_page(self):
        K = key(domains={"other.com"}, linkedin=None)
        assert rules.wrong_company(rec(name="Acme"), K) == []
        assert rules.wrong_company(rec(name="Acme Rocket Labs"), K)   # shares only a word

    def test_known_other_entities_and_parent(self):
        parent = {"name": "Mega Holdings", "domain": "mega.com", "employees": 5000}
        K = key(parent=parent, others=[parent, {"name": "Acme India", "linkedin": "https://www.linkedin.com/company/acme-india"}])
        assert any("parent" in w for w in rules.wrong_company(rec(emp_count=4500), K))
        assert any("LinkedIn page of Acme India" in w for w in rules.wrong_company(rec(li_slug="acme-india"), K))
        assert any("domain of Mega Holdings" in w for w in rules.wrong_company(rec(domain="mega.com"), K))


class TestAggregation:
    def rows(self):
        out = []
        for cid, g, a, b in [("A", "us_public", 1, 0), ("B", "us_public", 1, 1), ("C", "us_private", 0, 1)]:
            for s, v in (("x", a), ("y", b)):
                out.append({"company_id": cid, "group": g, "system": s, "field": "hq", "score": float(v), "answered": True})
        return out

    def test_groups_weigh_equally(self):
        t = rm.table(self.rows(), ["x", "y"], ["hq"])
        members = {"us_public": ["A", "B"], "us_private": ["C"]}
        assert rm.summarize(t["x"], members)["score"] == pytest.approx(0.5)    # (1.0 + 0.0) / 2, not 2/3
        assert rm.summarize(t["y"], members)["score"] == pytest.approx(0.75)

    def test_bootstrap_is_seeded_and_paired(self):
        t = rm.table(self.rows(), ["x", "y"], ["hq"])
        members = {"us_public": ["A", "B"], "us_private": ["C"]}
        b1, b2 = rm.bootstrap(t, ["x", "y"], members, 200, 7), rm.bootstrap(t, ["x", "y"], members, 200, 7)
        assert b1 == b2
        pairs, ranks = rm.compare(b1, {"x": 0.5, "y": 0.75}, ["x", "y"])
        assert len(pairs) == 1 and 0 <= pairs[0]["p_holm"] <= 1 and set(ranks) == {"x", "y"}

    def test_holm_is_monotone(self):
        boot = {"a": [0.9] * 50, "b": [0.5] * 50, "c": [0.5 + i / 1000 for i in range(50)]}
        pairs, _ = rm.compare(boot, {"a": 0.9, "b": 0.5, "c": 0.52}, ["a", "b", "c"])
        ps = sorted(pairs, key=lambda p: p["p"])
        assert all(x["p_holm"] <= y["p_holm"] for x, y in zip(ps, ps[1:])) and all(p["p_holm"] >= p["p"] for p in pairs)


def test_industry_without_description_or_lists_is_not_graded():
    # a company whose site sits behind a bot check has no description and no pre-registered labels: industry is not graded
    from company_enrichment.scorer import dataset
    row = {"id": "P1", "group": "us_private", "input": {"domain": "x.com"}, "company": {"name": "X"},
           "expected": {"industry": {"best_fit": [], "either_best_fit": [], "broad_ok": [], "description": None}}}
    K = dataset.key(row)
    assert K["industry"] is None and rules.key_has(K, "industry") is False
    row["expected"]["industry"]["description"] = "We build bridges."
    assert rules.key_has(dataset.key(row), "industry") is True
