# Company Enrichment: a benchmark for company data providers

Compare company data providers on how accurately they fill in a company's profile across seven fields, from nothing but its website.

This benchmark is run by Firecrawl. Apollo.io, FullEnrich and Data Legion are sold through [Firecrawl Alexandria](https://www.firecrawl.dev/alexandria), and People Data Labs is coming to it; Ocean.io and ContactOut are not. That is why the harness, a public half of the answer key and every answer-key correction are published here.

This repository ships a 102-company public sample (half of every group and size band). The rest is held back so no provider can tune to it.

## Results

<!-- dataset:start -->
200 US companies: 150 public, 50 private. One lookup per company, the website domain as the only input. Run on October 2, 2026.
<!-- dataset:end -->

Common fields is the headline: the four fields scored by code alone (headquarters, employee count, founding year and LinkedIn page). All fields adds industry, which AI judges partly grade, and stock ticker and revenue, which only some providers return. Scores are the share of points, with 95% confidence intervals. `[alexandria]` providers are available through [Firecrawl Alexandria](https://www.firecrawl.dev/alexandria); `[soon]` is coming to it.

<!-- results:start -->
| provider | common score | 95% CI | all fields | 95% CI | coverage | accuracy when answered | public sample (n=102) |
|---|---|---|---|---|---|---|---|
| Apollo.io `[alexandria]` | **89.3%** | [86.0%, 92.2%] | 81.6% | [78.3%, 84.5%] | 99% | 92% | 88.9% |
| People Data Labs `[soon]` | 80.6% | [76.5%, 84.3%] | 72.0% | [68.0%, 75.5%] | 98% | 83% | 78.8% |
| Ocean.io | 78.7% | [74.4%, 82.7%] | 64.9% | [61.3%, 68.2%] | 97% | 81% | 75.5% |
| FullEnrich `[alexandria]` | 72.7% | [67.8%, 77.3%] | 59.6% | [55.1%, 63.6%] | 89% | 81% | 69.0% |
| ContactOut | 72.7% | [68.4%, 76.8%] | 60.6% | [56.6%, 64.4%] | 88% | 83% | 69.3% |
| Data Legion `[alexandria]` | 52.2% | [49.2%, 55.0%] | 54.1% | [51.1%, 57.0%] | 69% | 72% | 50.1% |

| provider | hq (200 of 200) | employees (187 of 200) | founded (139 of 200) | linkedin (156 of 200) | industry (extra, 199 of 200) | ticker (extra, 150 of 200) | revenue (extra, 145 of 200) |
|---|---|---|---|---|---|---|---|
| Apollo.io | **91%** | **77%** | **89%** | **99%** | 53% | **91%** | **83%** |
| People Data Labs | 89% | 52% | 85% | 99% | 63% | 90% | 5% |
| Ocean.io | 85% | 57% | 81% | 94% | **69%** | not offered | 2% |
| FullEnrich | 76% | 54% | 68% | 93% | 63% | not offered | not offered |
| ContactOut | 65% | 55% | 79% | 96% | 65% | not offered | 8% |
| Data Legion | not offered | 46% | 84% | 96% | 64% | 89% | not offered |
<!-- results:end -->

**Common fields:** On the four common fields, Apollo.io leads by 8.7 points. Apollo.io (89.3%) ranks first, People Data Labs (80.6%) and Ocean.io (78.7%) are statistically tied for second, FullEnrich (72.7%) and ContactOut (72.7%) are statistically tied for fourth, and Data Legion (52.2%) is last. Every other gap is statistically significant.

**All fields:** With the three extra fields added, industry, ticker and revenue, Apollo.io leads by 9.6 points. Apollo.io (81.6%) ranks first, People Data Labs (72.0%) is second, Ocean.io (64.9%) is third, ContactOut (60.6%) and FullEnrich (59.6%) are statistically tied for fourth, and Data Legion (54.1%) is last. Every other gap is statistically significant.

Gaps are tested with a paired bootstrap over companies and a Holm correction across all 15 pairs of providers.

The second table shows every field for all 6 providers. The best value in each column is highlighted. Columns marked extra are left out of the headline score, and "not offered" means the provider does not return that field, which counts as 0. Next to each field in the second table is the number of the 200 companies it is graded on. Coverage, accuracy when answered and the public-sample column are on the four common fields. The public-sample column is what you can reproduce from this repository, and the number to compare your own run with. Data Legion is scored on its Premium record, its fullest company record. Each provider is graded on its own employee estimate, never on a LinkedIn member count it passes through.

### Answer-key corrections

After the provider calls, the answer key was audited and 67 entries were changed: 29 founding years, 17 company identities, 15 employee counts, 3 headquarters, 2 LinkedIn pages and 1 metro area. Some fix a value, some replace the evidence behind an unchanged value, and some stop grading a field whose source does not cover it; each entry in the list says which. Each change is backed by a primary source and applies to every provider. The all-fields ranking is the same before and after the corrections. Before them and four fixes to how providers' addresses are read, the all-fields scores were Apollo.io 79.3%, People Data Labs 69.6%, Ocean.io 63.5%, ContactOut 58.8%, FullEnrich 57.8% and Data Legion 52.7%; together they raised every provider's all-fields score by 1.4 to 2.4 points.

The 42 corrections on public-sample companies are listed in full in [`benchmark/results/corrections.json`](benchmark/results/corrections.json), with the source and the exact words behind each one. The 25 on held-out companies are counted by field, so the held-out half stays unpublished.

## Install

```bash
uv sync --extra dev        # Python 3.11+; creates .venv and installs everything, including pytest
cp .env.example .env       # add only the keys of the providers you run
uv run pytest company_enrichment/tests -q
```

With pip instead: `pip install -e ".[dev]"`. Both install the `company-enrichment` and `company-enrichment-report`
commands. The examples below use `uv run`; pip users can drop it.

Scoring is free and offline. Calling a provider spends its credits. Smoke-test with `--limit` first.

## Run a provider

```bash
uv run company-enrichment --provider apollo --limit 10
uv run company-enrichment-report
```

- `--provider`: `apollo`, `fullenrich`, `datalegion` (through Firecrawl Alexandria, `FIRECRAWL_API_KEY`), `pdl`
  (`PDL_API_KEY`), `ocean` (`OCEAN_API_KEY`), `contactout` (`CONTACTOUT_API_KEY`). People Data Labs, Ocean.io and
  ContactOut are called through their own APIs, so running them needs your own API key for each. Drop `--limit` for
  the whole public sample.
- Before any call, a preflight looks up three probe companies (not in the answer key) and checks that the provider
  still returns every field the benchmark reads, as listed in `harness/fields_manifest.json`. If a provider renamed a
  field, the run stops instead of quietly scoring 0 on it.
- The report scores the newest run of every label under `company_enrichment/runs/`; `--label apollo,ocean` picks some,
  `--out results.json` saves them, `--verdicts verdicts.csv` writes one row per company, provider and field.
- A US city missing from the metro table can only score on an exact match; `--geocode` looks it up (free, slow). An
  industry label nobody has graded is left out and listed; `--judge` grades it (paid, about $0.02 a label).

## Run your own provider

```bash
uv run python benchmark/run_benchmark.py --name yourco \
    --endpoint 'https://api.yourco.com/enrich?domain={domain}' --auth "Bearer $KEY" --limit 10
```

Drop `--limit` for the full public sample. `--report-only --out results.json` re-scores the run on disk. Your
endpoint is called exactly like every provider above: one company per request, the domain as the only input, and
a failed or empty answer counts as 0.

**What your endpoint returns** (JSON; any field may be null):

| field | meaning |
|---|---|
| `company_name` | the company's name |
| `website` | its primary website domain |
| `hq_city`, `hq_state`, `hq_country` | headquarters |
| `employee_count` | your latest total headcount estimate (a number) |
| `employee_range` | a size band such as `1001-5000`, used only when there is no count |
| `industry` | one label; LinkedIn industry names score by code, any other label goes to the judges |
| `founded_year` | the year founded |
| `linkedin_url`, `linkedin_id` | the company's LinkedIn page, and its numeric page ID if you have it |
| `stock_ticker` | for a public company |
| `annual_revenue_usd` | the latest annual revenue in US dollars |

Use `{domain}` in the URL for a GET, or leave it out and the domain is POSTed as `{"domain": "..."}`. If your API
names its fields differently, put a small shim in front of it that renames them. The shim must not look anything up
anywhere else.

See [CONTRIBUTING.md](CONTRIBUTING.md) to submit a score for the table.

## Method

**Headquarters**. The principal executive office: the newest SEC filing's cover for public companies, federal labor filings for private ones. The same city, or another city in the same US metro area, is correct. Graded on 200 of 200 companies.

**Employee count**. Total headcount from the latest annual report, or from federal payroll and pension filings for private companies. A count within 1.2x of the filed range scores 1, within 2x scores 0.5. Graded on 187 of 200 companies.

**Founding year**. The year the business started; a holding company's or IPO vehicle's later incorporation year does not count. Within one year is correct. Graded on 139 of 200 companies.

**LinkedIn page**. The company's own LinkedIn page, as linked from its website. Any address of the same page counts. Graded on 156 of 200 companies.

**Industry** `[extra]`. A label from LinkedIn's industry list. Before any provider was called, two AI models picked the best-fit labels from each company's own description; a best fit scores 1, a broader or secondary label 0.75. A label outside those lists goes to five AI judges, so industry is an extra field. Graded on 199 of 200 companies.

**Stock ticker** `[extra]`. Any of the company's common-stock tickers. Public companies only. Graded on 150 of 200 companies.

**Revenue** `[extra]`. The latest fiscal year's revenue from SEC filings, within 5%. Public companies only. Graded on 145 of 200 companies.

**Common fields and all fields**. Common fields, the headline, is the share of points on the four common fields, scored by code alone: headquarters, employee count, founding year and LinkedIn page. A provider that does not return one of them, such as Data Legion for headquarters, scores 0 on it. All fields adds the three extra fields: industry, which AI judges partly grade, and ticker and revenue, which only some providers return. US public and US private companies weigh equally.

A missing answer, an error or a field the provider does not offer scores 0, so the score reflects coverage as well as accuracy. A record about a different company, such as the parent, scores 0 on every field.

**How providers are called**. Every provider gets the same input, the company's website domain, in one lookup per company with default settings. Apollo.io, FullEnrich and Data Legion are called through Firecrawl Alexandria; People Data Labs, Ocean.io and ContactOut through their own APIs. In this run, People Data Labs was reached through Firecrawl Alexandria's People Data Labs capability, which calls the same PDL company enrichment API; a direct call returns the same fields. Each provider is scored on its fullest company record. Every call in the run succeeded on the first try; none failed or was rate-limited.

**The answer key**. Every value comes from SEC filings, federal records or the company's own website, with the exact words it was taken from. A field with no primary source is not graded for any provider. The key was frozen before any provider was called. Two kinds of change came after the calls, applied to every provider alike: each LinkedIn address a provider returned was resolved, and the ones that lead to the company's own page (its numeric page ID or an older address) were added to the key; and the corrections listed above.

AI models helped build and review the key: they extracted candidate values from filings and websites and cross-checked them. Every value carries the exact quote from its source, checked word for word, and its `method` field says how it was found.

Companies were drawn by ranking each pool on a hash of a fixed seed. A company whose website belongs to a different legal entity, such as a foreign parent, was replaced by the next one in that order.

**Confidence intervals**. 95% intervals come from 5,000 bootstrap resamples of companies. A gap between two providers counts as significant only after a Holm correction across all 15 pairs.

One rule per field, in [`company_enrichment/scorer/rules.py`](company_enrichment/scorer/rules.py). Details the field
descriptions above leave out:

- *Headquarters:* a documented second location scores 0.5. A different country scores 0. Outside the US only the exact
  city counts.
- *Employees:* under 50 employees, a count within 5 of the range also scores 1. A size band is scored at its geometric
  midpoint, an open top band (`10001+`) at its lower bound.
- *Ticker:* class punctuation is ignored (`BRK.B` = `BRK-B`).
- *Revenue:* a band is scored at its midpoint, so a provider that returns only a revenue range rarely lands within 5%.
- *Industry:* the judges' votes ship in `company_enrichment/gt/industry_grades_*.jsonl`, so scoring needs no model call.
- *Wrong company:* a record whose website is not the company's counts as a different company unless its LinkedIn page
  is the company's or, when there is no page to compare, its name matches. A record that matches the parent or another
  entity the key records counts the same way.

## Dataset

200 companies scored on the full set. A 102-company public sample, about half of each group, ships for reproduction.

**US public companies.** 150 companies drawn at random from NYSE and Nasdaq SEC filers, in four size bands from large caps to micro caps. Their answer key comes from SEC filings and their own websites. Example: amgen.com: Thousand Oaks, California · Pharmaceutical Manufacturing · 31,500 employees · founded 1980 · linkedin.com/company/amgen · AMGN · $36.8 billion revenue.

**US private companies.** 50 companies drawn at random from employers in the Department of Labor's PERM disclosures whose employer ID also files a Form 5500 pension plan, in four size bands. Their answer key comes from those federal filings and their own websites. Example: providertrust.com: Nashville, Tennessee · Software Development · 116 to 120 employees · founded 2010 · linkedin.com/company/providertrust.

<!-- sample:start -->
| | full (held back) | public sample (shipped) |
|---|---|---|
| US public | 150 | 76 |
| US private | 50 | 26 |
<!-- sample:end -->

One JSON line per company in `company_enrichment/gt/companies_public.jsonl`:

```json
{"id": "...", "group": "us_public", "stratum": "large", "subset": "public",
 "input": {"domain": "example.com"},
 "company": {"name": "...", "names": ["..."], "domains": ["..."], "parent": null, "known_other": []},
 "expected": {"hq": {"city": "...", "state": "MA", "country": "US", "alternatives": []},
              "employees": {"low": 22050, "high": 26950}, "founded": {"year": 1965},
              "linkedin": {"slugs": ["..."], "ids": ["..."]}, "ticker": ["..."], "revenue": {"usd": 11019707000, "prior_usd": null},
              "industry": {"best_fit": ["..."], "either_best_fit": ["..."], "broad_ok": ["..."], "description": "..."}},
 "provenance": {"hq": [{"source_url": "https://www.sec.gov/...", "locator": "...", "quote": "...", "sha256": "..."}]},
 "audit": {"corrections": []}}
```

`benchmark/` the runner for your own provider, `benchmark/results/results.json` and `benchmark/results/corrections.json` ·
`company_enrichment/gt/` the public sample, industry grades, metro table and LinkedIn's industry taxonomy ·
`company_enrichment/harness/` provider adapters, preflight, field manifest and extraction · `company_enrichment/scorer/`
produced every number above · `company_enrichment/tests/` offline tests

## Changelog

- October 2, 2026: First published run. 6 providers on 200 US companies (150 public, 50 private), scored on four common fields and three extra fields: industry, ticker and revenue. Answer-key corrections made after the provider calls are listed in the repository.

## License

The code is under the MIT License ([`LICENSE`](LICENSE)). The data Firecrawl compiled, in `company_enrichment/gt/` and
`benchmark/results/`, is under CC BY 4.0 ([`LICENSE-DATA`](LICENSE-DATA)). Quotes from companies' websites and
LinkedIn's industry list belong to their owners; see [`NOTICE`](NOTICE). Provider names are trademarks of their owners.
