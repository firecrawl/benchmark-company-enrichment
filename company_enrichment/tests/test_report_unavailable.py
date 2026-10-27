"""Unavailable comparisons must preserve reports and must not imply significance."""
import csv
import json
import sys

import pytest

from company_enrichment.scorer import report_metrics as rm, rules


def result(scores):
    systems = {}
    for name, score in scores.items():
        metric = {"score": score, "ci95": [score, score]}
        systems[name] = {"common": metric, "all_fields": metric, "coverage": None,
                         "accuracy_when_answered": None, "fields": {
                             field: {"score": score, "offered": score is not None, "graded": 0}
                             for field in rules.ALL}}
    return {"systems": systems, "pairwise": {"common": []}, "companies_scored": {}}


def test_no_pairwise_samples_are_reported_as_unavailable():
    text = rm.markdown(result({"one": None, "two": None}))
    assert "| one |" in text and "| two |" in text
    assert "comparison is unavailable" in text
    assert "not statistically significant" not in text
    assert "**—**" not in text


def test_zero_score_ranks_ahead_of_missing_score_without_claiming_comparison():
    text = rm.markdown(result({"missing": None, "zero": 0.0}))
    lines = text.splitlines()
    zero = next(i for i, line in enumerate(lines) if line.startswith("| zero |"))
    missing = next(i for i, line in enumerate(lines) if line.startswith("| missing |"))
    assert zero < missing
    assert "| zero | **0.0%**" in text
    assert "comparison is unavailable" in text
    assert "not statistically significant" not in text


def test_disjoint_runs_still_write_json_and_verdicts(tmp_path, monkeypatch, capsys):
    rows = [{"id": cid, "group": "us_public", "company": {"name": "Example"},
             "input": {"domain": "example.com"}, "expected": {"founded": {"year": 2000}}}
            for cid in ("C1", "C2")]
    for index, label in enumerate(("one", "two")):
        run = tmp_path / f"20261026T00000{index}Z-{label}"
        (run / "records").mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps({"provider": "external", "label": label}))
        (run / "records" / f"C{index + 1}.json").write_text(json.dumps({"http_status": 200, "response": {
            "company_name": "Example", "website": "example.com", "founded_year": 2000}}))
    monkeypatch.setattr(rm.dataset, "load", lambda _: rows)
    out, verdicts = tmp_path / "results.json", tmp_path / "verdicts.csv"
    monkeypatch.setattr(sys, "argv", ["report", "--runs", str(tmp_path), "--out", str(out),
                                     "--verdicts", str(verdicts), "--resamples", "20"])
    assert rm.main() == 0
    report = json.loads(out.read_text())
    assert report["companies_scored"] == {}
    assert report["excluded"] == {"C1": "not run on two", "C2": "not run on one"}
    assert report["pairwise"]["common"] == []
    assert all(system["common"]["score"] is None for system in report["systems"].values())
    with verdicts.open(newline="") as stream:
        reader = csv.DictReader(stream)
        assert "company_id" in reader.fieldnames and list(reader) == []
    assert "comparison is unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("significant", [False, True])
def test_available_pair_retains_existing_significance_statement(significant):
    report = result({"low": 0.6, "high": 0.8})
    report["pairwise"]["common"] = [{"a": "low", "b": "high", "significant": significant}]
    text = rm.markdown(report)
    assert "comparison is unavailable" not in text
    assert ("not statistically significant" in text) is (not significant)
    assert "| high | **80.0%**" in text
