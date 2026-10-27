"""Requested run labels must not silently collapse to one display-name entry."""
import json
import sys

import pytest

from company_enrichment.scorer import report_metrics as rm


def save_run(root, folder, provider, label):
    run = root / folder
    (run / "records").mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"provider": provider, "label": label}))
    return run


@pytest.mark.parametrize("provider", ["external", "pdl"])
def test_colliding_display_names_are_rejected_before_grading(tmp_path, monkeypatch, provider):
    first = save_run(tmp_path, "20261026T000000Z-apollo", "apollo", "apollo")
    second = save_run(tmp_path, "20261026T000001Z-custom", provider, "Apollo.io")
    monkeypatch.setattr(sys, "argv", ["report", "--runs", str(tmp_path), "--label", "apollo,Apollo.io"])
    monkeypatch.setattr(rm.dataset, "load", lambda _: [])
    def grade(*args):
        raise AssertionError(f"grading started after a requested system was overwritten: {list(args[1])}")
    monkeypatch.setattr(rm, "grade", grade)
    with pytest.raises(SystemExit, match="same display name") as error:
        rm.main()
    message = str(error.value)
    assert "apollo" in message and "Apollo.io" in message
    assert first.name in message and second.name in message


def test_distinct_display_names_preserve_both_runs_and_provenance(tmp_path, monkeypatch, capsys):
    first = save_run(tmp_path, "20261026T000000Z-apollo", "apollo", "apollo")
    second = save_run(tmp_path, "20261026T000001Z-candidate", "external", "candidate")
    (first / "records/C1.json").write_text(json.dumps({"http_status": 200, "response": {
        "organization": {"name": "Acme", "primary_domain": "example.com", "founded_year": 2000}}}))
    (second / "records/C1.json").write_text(json.dumps({"http_status": 200, "response": {
        "company_name": "Acme", "website": "example.com", "founded_year": 2000}}))
    rows = [{"id": "C1", "group": "us_public", "company": {"name": "Acme"},
             "input": {"domain": "example.com"}, "expected": {"founded": {"year": 2000}}}]
    monkeypatch.setattr(rm.dataset, "load", lambda _: rows)
    out = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["report", "--runs", str(tmp_path), "--resamples", "20", "--out", str(out)])
    assert rm.main() == 0
    result = json.loads(out.read_text())
    assert set(result["systems"]) == {"Apollo.io", "candidate"}
    assert result["systems"]["Apollo.io"]["run"] == first.name
    assert result["systems"]["candidate"]["run"] == second.name
    assert len(result["pairwise"]["common"]) == 1
    capsys.readouterr()
