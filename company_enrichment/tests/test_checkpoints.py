"""Completed provider calls survive an interruption elsewhere in the run."""
import json
from pathlib import Path
from threading import Event

import pytest

from company_enrichment.harness import providers, runner


def test_fast_answer_is_checkpointed_before_slow_call_finishes(tmp_path, monkeypatch):
    saved = Event()
    write_text = Path.write_text
    replace = Path.replace
    def observe_write(path, *args, **kwargs):
        result = write_text(path, *args, **kwargs)
        if path.name == "C2.json": saved.set()
        return result
    def observe_replace(path, target):
        result = replace(path, target)
        if Path(target).name == "C2.json": saved.set()
        return result
    monkeypatch.setattr(Path, "write_text", observe_write)
    monkeypatch.setattr(Path, "replace", observe_replace)
    calls = []
    def call(provider, domains):
        calls.extend(domains)
        if domains == ["slow.example"]:
            assert saved.wait(2), "completed fast response was not checkpointed"
            raise KeyboardInterrupt
        return {d: providers.Result(200, {"name": "Fast"}) for d in domains}
    monkeypatch.setattr(providers, "call", call)
    rows = [{"id": "C1", "input": {"domain": "slow.example"}},
            {"id": "C2", "input": {"domain": "fast.example"}}]
    with pytest.raises(KeyboardInterrupt):
        runner.run("external", rows, out_root=tmp_path, workers=2)
    record = next(tmp_path.glob("*/records/C2.json"))
    assert json.loads(record.read_text())["response"] == {"name": "Fast"}
    resumed = []
    def resume_call(provider, domains):
        resumed.extend(domains)
        return {d: providers.Result(200, {"name": "Slow"}) for d in domains}
    monkeypatch.setattr(providers, "call", resume_call)
    runner.run("external", rows, out_root=tmp_path, resume=True)
    assert resumed == ["slow.example"]


def test_completed_batches_survive_interruption_in_next_batch(tmp_path, monkeypatch):
    rows = [{"id": f"C{i}", "input": {"domain": f"c{i}.example"}} for i in range(31)]
    def call(provider, domains):
        if "c30.example" in domains:
            assert len(list(tmp_path.glob("*/records/*.json"))) == 30
            raise KeyboardInterrupt
        return {d: providers.Result(200, {"name": d}) for d in domains}
    monkeypatch.setattr(providers, "call", call)
    with pytest.raises(KeyboardInterrupt):
        runner.run("contactout", rows, out_root=tmp_path)
    assert len(list(tmp_path.glob("*/records/*.json"))) == 30


def test_initial_failure_is_checkpointed_before_retry_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(providers, "call", lambda provider, domains: {d: providers.Result(503, error="busy") for d in domains})
    def interrupt_wait(seconds):
        rec = next(tmp_path.glob("*/records/C1.json"), None)
        assert rec is not None, "initial failure was not saved before retry sleep"
        assert json.loads(rec.read_text())["http_status"] == 503
        raise KeyboardInterrupt
    monkeypatch.setattr(runner.time, "sleep", interrupt_wait)
    with pytest.raises(KeyboardInterrupt):
        runner.run("external", [{"id": "C1", "input": {"domain": "acme.example"}}], out_root=tmp_path)


def test_interrupted_checkpoint_preserves_previous_valid_json(tmp_path, monkeypatch):
    path = tmp_path / "C1.json"
    original = {"http_status": 503, "error": "busy"}
    path.write_text(json.dumps(original))
    def interrupted_replace(source, target):
        raise OSError("simulated interruption before atomic replacement")
    monkeypatch.setattr(Path, "replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        runner._write_json(path, {"http_status": 200, "response": {"name": "Acme"}})
    assert json.loads(path.read_text()) == original
    assert list(tmp_path.glob("*.json")) == [path]
