"""Preflight and the fields manifest. No credentials, no calls.

The manifest lists every response path records.py reads. These tests keep the two in step (a new field read without a
manifest entry fails here) and pin that preflight stops a run when a provider renames or drops a required field.
"""
import ast
import json
from pathlib import Path

import pytest

from company_enrichment.harness import preflight, records

APOLLO = {"organization": {"name": "Acme", "primary_domain": "acme.com", "linkedin_url": "linkedin.com/company/acme",
                           "city": "Austin", "state": "TX", "country": "United States", "estimated_num_employees": 900,
                           "industry": "software", "founded_year": 1998, "publicly_traded_symbol": "ACME",
                           "annual_revenue": 1.2e9}}


def test_paths():
    body = {"a": [{"b": 1}, {"b": 2}], "c": {"d": [None, 5]}}
    assert preflight.values(body, "a[].b") == [1, 2] and preflight.values(body, "a[0].b") == [1]
    assert preflight.values(body, "c.d[1]") == [5] and preflight.values(body, "c.x.y") == []


def test_passes_when_every_required_path_has_a_value_somewhere():
    second = {"organization": {"name": "Beta", "primary_domain": "beta.io"}}   # a private company: no ticker, that's fine
    rep = preflight.check("apollo", ["acme.com", "beta.io"], {"acme.com": APOLLO, "beta.io": second})
    assert rep["ok"] and rep["missing_required"] == []


def test_fails_when_a_provider_renames_a_field():
    renamed = json.loads(json.dumps(APOLLO))
    renamed["organization"]["employee_count"] = renamed["organization"].pop("estimated_num_employees")
    rep = preflight.check("apollo", ["acme.com"], {"acme.com": renamed})
    assert not rep["ok"] and rep["missing_required"] == ["organization.estimated_num_employees"]
    assert "organization.employee_count" in rep["unknown_paths"]


def test_fails_when_every_probe_errors():
    rep = preflight.check("ocean", ["acme.com"], {"acme.com": None})
    assert not rep["ok"] and rep["failed"]


def test_every_field_records_reads_is_in_the_manifest():
    segs = set()
    for spec in preflight.MANIFEST["providers"].values():
        for p in spec["required"] + spec["optional"]:
            segs |= {s for s in p.replace("[]", ".").replace("[0]", ".").split(".") if s}
    internal = {"p", "R"}   # records.py's own intermediate dicts, not provider answers
    read = set()
    for n in ast.walk(ast.parse(Path(records.__file__).read_text())):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) and not (isinstance(n.func.value, ast.Name) and n.func.value.id in internal):
            read.add(n.args[0].value)
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str) \
                and not (isinstance(n.value, ast.Name) and n.value.id in internal):
            read.add(n.slice.value)
    assert read <= segs, sorted(read - segs)


@pytest.mark.parametrize("provider", sorted(k for k, v in preflight.MANIFEST["providers"].items() if v["required"]))
def test_probes_are_not_in_the_shipped_answer_key(provider):
    from company_enrichment.scorer import dataset
    doms = {r["input"]["domain"] for r in dataset.load()}
    assert not set(preflight.MANIFEST["probe_domains"]) & doms
