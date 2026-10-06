"""Provider adapters and the runner, with the network replaced by a fake. No credentials, no calls.

They pin the call contract: the domain is the only input, batch answers are split back to the domain that was sent
(including Ocean.io's redirects and missing companies), a rate-limited call is sent again until it is answered, any
other failure is retried once and then recorded and stays in the denominator, and a resumed run skips companies
already answered on disk.
"""
import json

import pytest

from company_enrichment.harness import providers, runner


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def install(answer):
        def _post(url, body, headers, timeout=120):
            calls.append((url, body, headers))
            return answer(url, body, headers)

        def _get(url, headers, timeout=120):
            calls.append((url, None, headers))
            return answer(url, None, headers)
        monkeypatch.setattr(providers, "_post", _post)
        monkeypatch.setattr(providers, "_get", _get)
        return calls
    for k in ("FIRECRAWL_API_KEY", "PDL_API_KEY", "OCEAN_API_KEY", "CONTACTOUT_API_KEY"): monkeypatch.setenv(k, "test-key")
    return install


def test_alexandria_sends_only_the_domain_and_retries_rate_limits(fake, monkeypatch):
    answers = iter([{"data": {"alexandria": [{"error": {"code": "rate_limited"}}]}},
                    {"data": {"alexandria": [{"data": {"organization": {"name": "Acme"}}, "creditsCost": 30}]}}])
    calls = fake(lambda u, b, h: (200, next(answers), 5))
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    out = providers.call("apollo", ["acme.com"])
    assert calls[-1][1] == {"alexandria": [{"provider": "apollo", "capability": "companies/enrich", "options": {"domain": "acme.com"}}]}
    assert len(calls) == 2 and out["acme.com"].response == {"organization": {"name": "Acme"}} and out["acme.com"].cost == {"credits": 30}


def test_pdl_takes_the_domain_as_website(fake):
    calls = fake(lambda u, b, h: (200, {"status": 200, "name": "acme"}, 5))
    out = providers.call("pdl", ["acme.com"])
    assert calls[0][0] == "https://api.peopledatalabs.com/v5/company/enrich?website=acme.com"
    assert calls[0][2] == {"X-Api-Key": "test-key"} and out["acme.com"].response == {"status": 200, "name": "acme"}


def test_pdl_no_match_is_an_answer_and_rate_limits_are_retried(fake, monkeypatch):
    answers = iter([(429, {"status": 429, "error": {"type": "rate_limit"}}, 5),
                    (404, {"status": 404, "error": {"type": "not_found"}}, 5)])
    calls = fake(lambda u, b, h: next(answers))
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    r = providers.call("pdl", ["acme.com"])["acme.com"]
    assert len(calls) == 2 and r.http_status == 200 and r.response is None and r.error["code"] == "not_found"


def test_ocean_batch_split_redirects_and_missing(fake):
    fake(lambda u, b, h: (200, {"companies": [{"domain": "acme.com", "name": "Acme"},
                                              {"domain": "beta.io", "company": None},
                                              {"domain": "gamma.com", "name": "Gamma"}],
                                "redirectMap": {"gamma.co": "gamma.com"}}, 50))
    out = providers.call("ocean", ["acme.com", "beta.io", "gamma.co"])
    assert out["acme.com"].response["name"] == "Acme" and out["beta.io"].response is None
    assert out["gamma.co"].response["name"] == "Gamma"


def test_contactout_batches_of_thirty(fake):
    calls = fake(lambda u, b, h: (200, {"status_code": 200, "companies": {d: {"name": d} for d in b["domains"]}}, 5))
    out = providers.call("contactout", [f"c{i}.com" for i in range(31)])
    assert [len(c[1]["domains"]) for c in calls] == [30, 1] and out["c30.com"].response == {"name": "c30.com"}
    assert calls[0][2] == {"token": "test-key"}


def test_a_failed_call_is_retried_once_then_recorded(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    calls = fake(lambda u, b, h: (500, {"error": "boom"}, 5))
    rows = [{"id": "C1", "input": {"domain": "acme.com"}}]
    d = runner.run("fullenrich", rows, out_root=tmp_path)
    rec = json.loads((d / "records" / "C1.json").read_text())
    assert len(calls) == 2 and rec["http_status"] == 500 and rec["response"] is None and rec["error"]
    assert rec["first_attempt"]["http_status"] == 500   # both attempts kept; the company stays in the denominator


def test_a_retry_that_succeeds_replaces_the_failure(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    answers = iter([(503, {"error": "busy"}, 5), (200, {"data": {"alexandria": [{"data": {"companies": [{"name": "Acme"}]}}]}}, 5)])
    fake(lambda u, b, h: next(answers))
    d = runner.run("fullenrich", [{"id": "C1", "input": {"domain": "acme.com"}}], out_root=tmp_path)
    rec = json.loads((d / "records" / "C1.json").read_text())
    assert rec["http_status"] == 200 and rec["response"]["companies"][0]["name"] == "Acme" and rec["first_attempt"]["http_status"] == 503


def test_resume_skips_companies_on_disk(fake, tmp_path):
    calls = fake(lambda u, b, h: (200, {"data": {"alexandria": [{"data": {"companies": []}}]}}, 5))
    rows = [{"id": f"C{i}", "input": {"domain": f"c{i}.com"}} for i in range(3)]
    d1 = runner.run("fullenrich", rows[:2], out_root=tmp_path)
    d2 = runner.run("fullenrich", rows, out_root=tmp_path, resume=True)
    assert d1 == d2 and len(calls) == 3 and len(list((d2 / "records").glob("*.json"))) == 3


def test_missing_key_stops_before_any_call(monkeypatch, fake):
    calls = fake(lambda u, b, h: (200, {}, 5))
    monkeypatch.delenv("OCEAN_API_KEY")
    with pytest.raises(SystemExit):
        providers.call("ocean", ["acme.com"])
    assert not calls


def test_external_endpoint_get_and_post(monkeypatch, fake):
    calls = fake(lambda u, b, h: (200, {"company_name": "Acme"}, 5))
    monkeypatch.setenv("CE_EXT_URL", "https://api.example.com/enrich")
    monkeypatch.setenv("CE_EXT_AUTH", "Bearer x")
    out = providers.call("external", ["acme.com"])
    assert calls[0][1] == {"domain": "acme.com"} and calls[0][2] == {"Authorization": "Bearer x"}
    assert out["acme.com"].response == {"company_name": "Acme"}


RATE_LIMITED_ANSWER = {"data": {"alexandria": [{"error": {"code": "provider_rate_limited", "status": 429,
                                                          "message": "The data source is rate-limiting requests."}}]}}


def test_the_data_sources_rate_limit_is_retried_like_alexandrias(fake, monkeypatch):
    # a data source can answer HTTP 200 with provider_rate_limited: the call was not executed and must be sent again
    answers = iter([RATE_LIMITED_ANSWER, {"data": {"alexandria": [{"data": {"companies": [{"name": "Acme"}]}}]}}])
    calls = fake(lambda u, b, h: (200, next(answers), 5))
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    out = providers.call("fullenrich", ["acme.com"])
    assert len(calls) == 2 and out["acme.com"].response == {"companies": [{"name": "Acme"}]} and out["acme.com"].error is None


def test_runner_resends_a_rate_limited_call_until_answered(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    answers = iter([RATE_LIMITED_ANSWER] * 13 + [{"data": {"alexandria": [{"data": {"companies": [{"name": "Acme"}]}}]}}])
    calls = fake(lambda u, b, h: (200, next(answers), 5))
    d = runner.run("fullenrich", [{"id": "C1", "input": {"domain": "acme.com"}}], out_root=tmp_path)
    rec = json.loads((d / "records" / "C1.json").read_text())
    # 6 tries inside the adapter per round: rounds 1 and 2 stay rate-limited, round 3 is answered on its 2nd try
    assert len(calls) == 14 and rec["response"] == {"companies": [{"name": "Acme"}]} and rec["error"] is None
    assert rec["first_attempt"]["error"]["code"] == "provider_rate_limited" and rec["attempts"] == 3


def test_a_limit_that_never_lifts_is_recorded_as_failed(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    monkeypatch.setattr(runner, "RATE_LIMIT_ROUNDS", 2)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    fake(lambda u, b, h: (200, RATE_LIMITED_ANSWER, 5))
    d = runner.run("fullenrich", [{"id": "C1", "input": {"domain": "acme.com"}}], out_root=tmp_path)
    rec = json.loads((d / "records" / "C1.json").read_text())
    assert rec["response"] is None and rec["error"]["code"] == "provider_rate_limited" and rec["attempts"] == 3


def test_not_found_is_an_answer_and_is_not_sent_again(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    calls = fake(lambda u, b, h: (200, {"data": {"alexandria": [{"error": {"code": "not_found", "status": 404}}]}}, 5))
    d = runner.run("datalegion", [{"id": "C1", "input": {"domain": "acme.com"}}], out_root=tmp_path)
    rec = json.loads((d / "records" / "C1.json").read_text())
    assert len(calls) == 1 and rec["error"]["code"] == "not_found" and "first_attempt" not in rec


def test_resume_calls_rate_limited_records_again(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RETRY_WAIT_S", 0)
    monkeypatch.setattr(runner, "RATE_LIMIT_ROUNDS", 0)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    fake(lambda u, b, h: (200, RATE_LIMITED_ANSWER, 5))
    rows = [{"id": "C1", "input": {"domain": "acme.com"}}]
    d1 = runner.run("fullenrich", rows, out_root=tmp_path)
    calls = fake(lambda u, b, h: (200, {"data": {"alexandria": [{"data": {"companies": [{"name": "Acme"}]}}]}}, 5))
    before = len(calls)   # the fake keeps one list of calls for the whole test
    d2 = runner.run("fullenrich", rows, out_root=tmp_path, resume=True)
    rec = json.loads((d2 / "records" / "C1.json").read_text())
    assert d1 == d2 and len(calls) - before == 1 and rec["response"] == {"companies": [{"name": "Acme"}]}
