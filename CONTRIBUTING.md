# Contributing

## Submitting your provider's score

Your provider is called and scored exactly like every provider in the README: the same input (the website domain),
one lookup per company, the same extraction and the same scorer. There is one scorer; no submission gets a separate
path.

1. **Smoke-test first**, to check that your endpoint answers in the benchmark schema:

   ```bash
   uv run python benchmark/run_benchmark.py --name yourco \
       --endpoint 'https://api.yourco.com/enrich?domain={domain}' --auth "Bearer $KEY" --limit 10
   ```

   Then look at a few files under `company_enrichment/runs/<timestamp>-yourco/records/`: each holds your raw answer
   for one company.

2. **Run the full public sample:** drop `--limit`.

3. **Open a PR** with:
   - the exact command you ran;
   - a `results.json` written by

     ```bash
     uv run python benchmark/run_benchmark.py --name yourco --report-only --out results.json
     ```

     Pass the same `--name` you ran under: it is how your run directory is found.
   - anything about your API that the schema does not show (rate limits, which plan you used).

   Do not commit the `runs/` folder: those are your raw answers, and they stay yours.

To submit a provider, open a PR against the repo with your results on the public half, and email rafael@sideguide.dev
with valid API keys so we can rerun on the held-out half. The main table shows the full-set score. Never put an API key
in a PR, an issue or a commit.

## Changing the benchmark

A change to a scoring rule, the extraction of a field or the answer key changes the published numbers. Such a change
needs a new answer-key version (a new `companies_public.jsonl` and `version` in `results.json`), a re-run of every
provider, and a changelog entry in the README. `uv run pytest company_enrichment/tests -q` must pass. The tests pin
every rule with examples, so a rule change shows up as a failing test that you update on purpose.

If you think an answer-key value is wrong, open an issue with the company id, the field, and the primary source
(filing or official record) that shows the right value.
