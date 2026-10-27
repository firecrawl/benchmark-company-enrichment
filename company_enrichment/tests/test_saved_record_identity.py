"""Saved records must not contradict their run/provider and company filename."""
import json
import sys

import pytest

from company_enrichment.scorer import report_metrics as rm


def saved_run(root, record):
    run = root / "20261026T000000Z-candidate"
    (run / "records").mkdir(parents=True)
    (run / "meta.json").write_text(json.dumps({"provider": "external", "label": "candidate"}))
    (run / "records/C1.json").write_text(json.dumps(record))
    return run


@pytest.mark.parametrize("field,value", [("company_id", "C2"), ("company_id", None),
                                         ("provider", "apollo"), ("provider", None)])
def test_declared_record_identity_must_match_its_container(tmp_path, field, value):
    record = {"company_id": "C1", "provider": "external", "http_status": 200, "response": {}}
    record[field] = value
    run = saved_run(tmp_path, record)
    before = (run / "records/C1.json").read_bytes()
    with pytest.raises(ValueError, match=field) as error:
        rm.load_run(run)
    assert str(run / "records/C1.json") in str(error.value)
    assert (run / "records/C1.json").read_bytes() == before


@pytest.mark.parametrize("identity", [{}, {"company_id": "C1", "provider": "external"}])
def test_consistent_and_legacy_records_are_preserved(tmp_path, identity):
    record = {**identity, "http_status": 200, "response": {"company_name": "Example"}}
    run = saved_run(tmp_path, record)
    meta, loaded = rm.load_run(run)
    assert meta == {"provider": "external", "label": "candidate"}
    assert loaded == {"C1": record}


@pytest.mark.parametrize("field,value", [("company_id", "C2"), ("provider", "apollo")])
def test_report_rejects_mismatched_record_before_grading(tmp_path, monkeypatch, field, value):
    record = {"company_id": "C1", "provider": "external", "http_status": 200, "response": {}}
    record[field] = value
    saved_run(tmp_path, record)
    out = tmp_path / "results.json"
    monkeypatch.setattr(sys, "argv", ["report", "--runs", str(tmp_path), "--out", str(out)])
    monkeypatch.setattr(rm.dataset, "load", lambda _: [])
    def forbidden(*args, **kwargs):
        pytest.fail("contradictory record reached grading")
    monkeypatch.setattr(rm, "grade", forbidden)
    with pytest.raises(ValueError, match=field):
        rm.main()
    assert not out.exists()
