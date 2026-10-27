"""A mistaken smoke limit must never turn into an unbounded provider run."""
import runpy
import sys
from pathlib import Path

import pytest

from company_enrichment import run_eval
from company_enrichment.harness import providers, runner


def entrypoint(cli):
    if cli == "native": return run_eval.main, ["run", "--provider", "external"]
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "benchmark/run_benchmark.py"))
    return module["main"], ["run", "--name", "synthetic", "--endpoint", "https://example.com/enrich"]


@pytest.mark.parametrize("cli", ["native", "external"])
@pytest.mark.parametrize("limit", ["0", "-1"])
def test_invalid_cli_limit_stops_before_preflight_or_provider_work(cli, limit, monkeypatch, capsys):
    main, argv = entrypoint(cli)
    monkeypatch.setattr(sys, "argv", argv + ["--limit", limit])
    def forbidden(*args, **kwargs):
        pytest.fail("invalid smoke limit reached preflight or provider work")
    monkeypatch.setitem(main.__globals__, "load_env", forbidden)
    monkeypatch.setattr(run_eval.preflight, "run", forbidden)
    monkeypatch.setattr(runner, "run", forbidden)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "positive" in capsys.readouterr().err


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_direct_limit_leaves_no_artifacts_or_calls(tmp_path, monkeypatch, limit):
    calls = []
    def call(provider, domains):
        calls.extend(domains)
        return {d: providers.Result(200, {}) for d in domains}
    monkeypatch.setattr(providers, "call", call)
    rows = [{"id": f"C{i}", "input": {"domain": f"c{i}.example"}} for i in range(2)]
    with pytest.raises(ValueError, match="positive"):
        runner.run("external", rows, limit=limit, out_root=tmp_path)
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_positive_limit_calls_only_requested_prefix(tmp_path, monkeypatch):
    calls = []
    def call(provider, domains):
        calls.extend(domains)
        return {d: providers.Result(200, {}) for d in domains}
    monkeypatch.setattr(providers, "call", call)
    rows = [{"id": f"C{i}", "input": {"domain": f"c{i}.example"}} for i in range(3)]
    saved = runner.run("external", rows, limit=1, out_root=tmp_path)
    assert calls == ["c0.example"]
    assert [p.name for p in (saved / "records").glob("*.json")] == ["C0.json"]


@pytest.mark.parametrize("cli", ["native", "external"])
def test_positive_cli_limit_is_forwarded(cli, tmp_path, monkeypatch, capsys):
    from company_enrichment.scorer import report_metrics
    main, argv = entrypoint(cli)
    monkeypatch.setattr(sys, "argv", argv + ["--limit", "1"])
    monkeypatch.setitem(main.__globals__, "load_env", lambda: None)
    monkeypatch.setattr(run_eval.preflight, "run", lambda *a, **k: None)
    monkeypatch.setattr(run_eval.dataset, "load", lambda *a: [])
    monkeypatch.setattr(report_metrics, "main", lambda: 0)
    requested = []
    def run(*args, **kwargs):
        requested.append(kwargs["limit"])
        return tmp_path
    monkeypatch.setattr(runner, "run", run)
    main()
    assert requested == [1]
    capsys.readouterr()
