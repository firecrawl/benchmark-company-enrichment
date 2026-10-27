"""Run identity and resumption must never mix answers from different experiments."""
import json

import pytest

from company_enrichment.harness import providers, runner


ROWS = [{"id": "C1", "input": {"domain": "acme.example"}}]


@pytest.fixture
def calls(monkeypatch):
    seen = []
    def call(provider, domains):
        seen.append((provider, domains))
        return {d: providers.Result(200, {"company_name": str(len(seen))}) for d in domains}
    monkeypatch.setattr(providers, "call", call)
    monkeypatch.setattr(runner.time, "strftime", lambda *a: "20261026T120000Z")
    return seen


def test_fresh_runs_in_same_second_make_fresh_calls(tmp_path, calls):
    first = runner.run("external", ROWS, out_root=tmp_path)
    second = runner.run("external", ROWS, out_root=tmp_path)
    assert first != second
    assert len(calls) == 2
    assert json.loads((first / "records/C1.json").read_text())["response"] != json.loads((second / "records/C1.json").read_text())["response"]


@pytest.mark.parametrize("change", ["provider", "dataset", "domain"])
def test_incompatible_resume_fails_before_mutating_run(tmp_path, calls, change):
    first = runner.run("external", ROWS, label="candidate", dataset="public", out_root=tmp_path)
    before = {p.relative_to(first): p.read_bytes() for p in first.rglob("*.json")}
    rows = [{"id": "C1", "input": {"domain": "different.example"}}] if change == "domain" else ROWS
    with pytest.raises(ValueError, match=change):
        runner.run("pdl" if change == "provider" else "external", rows, label="candidate",
                   dataset="heldout" if change == "dataset" else "public", out_root=tmp_path, resume=True)
    assert len(calls) == 1
    assert {p.relative_to(first): p.read_bytes() for p in first.rglob("*.json")} == before


def test_resume_treats_label_as_literal_not_glob(tmp_path, calls):
    first = runner.run("external", ROWS, label="candidate[1]", out_root=tmp_path)
    resumed = runner.run("external", ROWS, label="candidate[1]", out_root=tmp_path, resume=True)
    assert resumed == first and len(calls) == 1


def test_exclusive_reservation_retries_a_name_collision(tmp_path, monkeypatch):
    ticks = iter([10, 10, 11])
    monkeypatch.setattr(runner.time, "time_ns", lambda: next(ticks))
    first = runner.run_dir("external", tmp_path)
    second = runner.run_dir("external", tmp_path)
    assert first != second and first.is_dir() and second.is_dir()


def test_reporting_finds_the_latest_isolated_run(tmp_path, calls):
    from company_enrichment.scorer.report_metrics import find_runs
    first = runner.run("external", ROWS, out_root=tmp_path)
    second = runner.run("external", ROWS, out_root=tmp_path)
    assert first != second
    assert find_runs(tmp_path, ["external"]) == {"external": second}


def test_resume_does_not_confuse_labels_with_shared_suffix(tmp_path, calls):
    other = runner.run("external", ROWS, label="foo-bar", out_root=tmp_path)
    own = runner.run("external", ROWS, label="bar", out_root=tmp_path, resume=True)
    assert own != other and len(calls) == 2


def test_external_resume_rejects_endpoint_change_without_recording_secrets(tmp_path, calls, monkeypatch):
    first_url = "https://api.example/enrich?api_key=secret-one"
    monkeypatch.setenv("CE_EXT_URL", first_url)
    first = runner.run("external", ROWS, out_root=tmp_path)
    assert "secret-one" not in (first / "meta.json").read_text()
    before = (first / "meta.json").read_bytes()
    monkeypatch.setenv("CE_EXT_URL", "https://other.example/enrich?api_key=secret-two")
    with pytest.raises(ValueError, match="endpoint"):
        runner.run("external", ROWS, out_root=tmp_path, resume=True)
    assert len(calls) == 1 and (first / "meta.json").read_bytes() == before
