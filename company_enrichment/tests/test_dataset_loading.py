"""Runtime dataset identity validation applies to custom and held-out inputs too."""
import json
import runpy
import sys
from pathlib import Path

import pytest

from company_enrichment import run_eval
from company_enrichment.scorer import dataset


@pytest.mark.parametrize("domain", ["example.com", "example.org"])
def test_duplicate_ids_are_rejected_with_both_line_numbers(tmp_path, domain):
    first = {"id": "C1", "input": {"domain": "example.com"}}
    duplicate = {"id": "C1", "input": {"domain": domain}}
    path = tmp_path / "companies_custom.jsonl"
    path.write_text("\n" + json.dumps(first) + "\n\n" + json.dumps(duplicate) + "\n")
    with pytest.raises(ValueError, match="duplicate company id") as error:
        dataset.load(str(path))
    message = str(error.value)
    assert str(path) in message and "C1" in message
    assert "line 4" in message and "line 2" in message


def test_unique_ids_keep_order_blank_lines_and_shared_domains(tmp_path):
    rows = [{"id": cid, "input": {"domain": "example.com"}} for cid in ("C2", "C1")]
    path = tmp_path / "companies_custom.jsonl"
    path.write_text("\n\n".join(json.dumps(row) for row in rows) + "\n")
    assert dataset.load(str(path)) == rows


@pytest.mark.parametrize("cli", ["native", "external"])
def test_duplicate_input_stops_both_clis_before_provider_work(tmp_path, monkeypatch, cli):
    row = {"id": "C1", "input": {"domain": "example.com"}}
    path = tmp_path / "companies_public.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    monkeypatch.setattr(dataset, "GT", tmp_path)
    if cli == "native":
        main, argv = run_eval.main, ["run", "--provider", "external"]
    else:
        main = runpy.run_path(str(Path(__file__).resolve().parents[2] / "benchmark/run_benchmark.py"))["main"]
        argv = ["run", "--name", "synthetic", "--endpoint", "https://example.com/enrich"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setitem(main.__globals__, "load_env", lambda: None)
    def forbidden(*args, **kwargs):
        pytest.fail("ambiguous dataset reached preflight or provider work")
    monkeypatch.setattr(run_eval.preflight, "run", forbidden)
    monkeypatch.setattr(run_eval.runner, "run", forbidden)
    with pytest.raises(ValueError, match="duplicate company id"):
        main()
