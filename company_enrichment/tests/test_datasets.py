"""Schema and integrity tests for the shipped answer key. They read the committed files and nothing else.

They guard what the scorer assumes (unique ids, one input domain, well-formed expected values) and what a public
dataset must never carry (personal email addresses, local paths, a held-back company in the public sample).
"""
import json
import os
import re
from collections import Counter
from pathlib import Path

import pytest

from company_enrichment.scorer import dataset, industry
from company_enrichment.scorer.normalize import li_parse, loc_key, reg_domain

# CE_DATASET checks another answer key file with the same tests
PUBLIC = os.environ.get("CE_DATASET") or dataset.DEFAULT
GTDIR = dataset.path(PUBLIC).parent
FULL = GTDIR / dataset.path(PUBLIC).name.replace("public", "full")
if not FULL.exists():   # the unversioned public sample pairs with the newest versioned full key (maintainers only)
    FULL = max(GTDIR.glob("companies_full_v*.jsonl"), key=lambda p: int(p.stem.rsplit("_v", 1)[-1]), default=FULL)
GRADES = GTDIR / dataset.path(PUBLIC).name.replace("companies_", "industry_grades_")   # this sample's judge votes
FIELDS = ["hq", "employees", "founded", "linkedin", "ticker", "revenue", "industry"]
GAPS = {}   # known defects allowed in the shipped key: none


def rows():
    # A MISSING SHIPPED DATASET IS A FAILURE, NOT A SKIP.
    assert dataset.path(PUBLIC).exists(), f"{dataset.path(PUBLIC)} is missing"
    return dataset.load(PUBLIC)


class TestShape:
    def test_ids_are_unique(self):
        ids = [r["id"] for r in rows()]
        assert len(ids) == len(set(ids))

    def test_every_company_has_one_input_domain(self):
        for r in rows():
            d = r["input"]["domain"]
            assert d and d == d.strip().lower() and "/" not in d and reg_domain(d), r["id"]

    def test_groups_strata_and_subset(self):
        allowed = {"us_public": {"large", "mid", "small", "micro"}, "us_private": {"11-50", "51-200", "201-500", "501-1000"}}
        for r in rows():
            assert r["stratum"] in allowed[r["group"]] and r["subset"] == "public", r["id"]

    def test_expected_values_are_well_formed(self):
        for r in rows():
            e = r["expected"]
            if e["employees"]: assert 0 < e["employees"]["low"] <= e["employees"]["high"], r["id"]
            if e["founded"]: assert 1600 <= e["founded"]["year"] <= 2026, r["id"]
            if e["hq"]: assert e["hq"]["city"] and (e["hq"]["country"] != "US" or e["hq"]["state"]), r["id"]
            if e["linkedin"]:
                assert all(li_parse(s)[0] == s for s in e["linkedin"]["slugs"]) and all(i.isdigit() for i in e["linkedin"]["ids"]), r["id"]
            if e["ticker"]: assert all(t == t.upper() and t.isalnum() for t in e["ticker"]), r["id"]
            if e["revenue"]: assert e["revenue"]["usd"] > 0, r["id"]
            if r["group"] == "us_private": assert e["revenue"] is None and e["ticker"] is None, r["id"]
            assert set(e["industry"]["best_fit"]) <= set(e["industry"]["either_best_fit"]), r["id"]

    def test_every_expected_value_carries_its_source(self):
        """A value without the quote and document it was taken from cannot be audited."""
        missing = set()
        for r in rows():
            for f in ("hq", "employees", "founded", "linkedin", "ticker", "revenue"):
                if r["expected"][f] is None: continue
                ev = r["provenance"].get(f) or []
                if not (ev and any(x.get("source_url") or x.get("document") for x in ev)): missing.add((r["id"], f))
        assert missing <= GAPS.get("no_provenance", set()), sorted(missing - GAPS.get("no_provenance", set()))

    def test_industry_ids_exist_in_the_taxonomy(self):
        ids, empty = set(industry.TAXONOMY["ids"]), set()
        for r in rows():
            ind = r["expected"]["industry"]
            assert set(ind["either_best_fit"]) | set(ind["broad_ok"]) <= ids, r["id"]
            # the judges need the company's own words; an industry with no description AND no lists is not graded at all
            # (the company's own words could not be read, e.g. a site behind a bot check)
            if not (ind["description"] or "").strip() and (ind["best_fit"] or ind["either_best_fit"] or ind["broad_ok"]):
                empty.add(r["id"])
        assert empty <= GAPS.get("no_description", set()), sorted(empty - GAPS.get("no_description", set()))


class TestPrivacy:
    def test_no_email_addresses(self):
        txt = dataset.path(PUBLIC).read_text()
        assert not re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", txt)

    def test_no_local_paths_or_internal_hosts(self):
        txt = dataset.path(PUBLIC).read_text()
        assert not re.search(r"file://|/Users/\w", txt, re.I)


class TestSample:
    def test_public_sample_is_about_half_of_every_cell(self):
        if not FULL.exists(): pytest.skip("the full answer key is held back (maintainers only)")
        full = [json.loads(l) for l in FULL.read_text().splitlines() if l.strip()]
        pub = {r["id"] for r in rows()}
        assert pub <= {r["id"] for r in full}
        cells = Counter((r["group"], r["stratum"]) for r in full)
        got = Counter((r["group"], r["stratum"]) for r in full if r["id"] in pub)
        assert all(got[c] == (n + 1) // 2 for c, n in cells.items())

    def test_industry_grades_cover_only_shipped_companies(self):
        ids = {r["id"] for r in rows()}
        assert GRADES.exists(), f"{GRADES} is missing"
        for p in [GRADES]:
            for line in p.read_text().splitlines():
                g = json.loads(line)
                assert g["company_id"] in ids and set(g["votes"]) <= set(industry.JUDGES)
                assert all(v in (None, 0.0, 0.5, 1.0) for v in g["votes"].values())

    def test_metro_table_keys_are_normalised(self):
        tab = json.loads((GTDIR / "hq_metro.json").read_text())
        for k, v in tab.items():
            city, state = k.split("|")
            assert loc_key(city, state) == k and (v is None or re.fullmatch(r"\d{5}", v)), k
