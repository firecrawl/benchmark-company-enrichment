"""The repository runs anywhere: nothing outside the package, no secrets, no machine-specific paths, and the
published numbers are the ones the scorer writes.
"""
import ast
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]          # the repository root
PKG = ROOT / "company_enrichment"
PY = [p for p in list(PKG.rglob("*.py")) + list((ROOT / "benchmark").rglob("*.py")) if "runs" not in p.parts]
ALLOWED = set(sys.stdlib_module_names) | {"company_enrichment", "tldextract", "anthropic", "pytest"}


def test_only_declared_dependencies_are_imported():
    found = set()
    for p in PY:
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Import): found |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0: found.add(n.module.split(".")[0])
    assert found <= ALLOWED, sorted(found - ALLOWED)
    deps = (ROOT / "pyproject.toml").read_text()
    assert "tldextract==" in deps and "anthropic" in deps


def test_no_machine_paths_or_internal_services():
    bad = re.compile(r"/Users/|/home/\w|file://", re.I)
    for p in PY + [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / ".env.example"]:
        if "tests" in p.relative_to(ROOT).parts: continue   # the tests hold these patterns as data
        hits = [m.group(0) for m in bad.finditer(p.read_text())]
        assert not hits, f"{p.relative_to(ROOT)}: {hits}"


def test_no_credentials_committed():
    pat = re.compile(r"\b(fc-[a-f0-9]{32}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|vck_[A-Za-z0-9]{20,})")
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix in {".py", ".md", ".json", ".jsonl", ".toml", ".yml", ".example", ".txt"}:
            assert not pat.search(p.read_text(errors="ignore")), p


def test_held_back_files_are_ignored():
    ign = (ROOT / ".gitignore").read_text()
    for pat in (".env", "company_enrichment/runs/", "companies_full_v*.jsonl", "industry_grades_full_v*.jsonl"):
        assert pat in ign, pat


def test_published_results_match_the_readme():
    from company_enrichment.scorer.report_metrics import markdown
    res = json.loads((ROOT / "benchmark/results/results.json").read_text())
    readme = (ROOT / "README.md").read_text()
    block = readme.split("<!-- results:start -->", 1)[1].split("<!-- results:end -->", 1)[0].strip()
    assert block == markdown(res).strip(), "README results differ from benchmark/results/results.json: re-export them"


def test_results_cover_every_shown_system():
    res = json.loads((ROOT / "benchmark/results/results.json").read_text())
    assert res["systems"] and sum(res["companies_scored"].values()) == res["dataset"]["companies"]
    from company_enrichment.scorer import rules
    assert res["fields"] == {"common": rules.COMMON, "all": rules.ALL}   # the published scores use today's field sets
    for s, x in res["systems"].items():
        assert x["common"]["ci95"][0] <= x["common"]["score"] <= x["common"]["ci95"][1], s
        assert set(x["fields"]) == set(res["fields"]["all"]), s
    n = len(res["systems"])
    assert len(res["pairwise"]["common"]) == n * (n - 1) // 2


@pytest.mark.parametrize("p", PY, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_module_parses_on_python_311(p):
    ast.parse(p.read_text(), feature_version=(3, 11))
