"""Industry grading: pre-registered label lists first, five AI judges for every other label.

Before any provider was called, two AI annotators from different model families each read the company's own business
description and picked LinkedIn industry IDs: best fit, and segments of at least about 10% of revenue. A provider label
that maps (by name) to a LinkedIn V2 industry ID is scored by code against those lists: a best fit of either annotator =
1, a broader parent category or a secondary segment = 0.75.

Every other label goes to five judges from three model families. Each sees the company's description, the label and
LinkedIn's definition of it, never the provider's name, and grades 1 / 0.5 / 0. The median counts (0.5 is reported as
0.75, like the lists). The old and new names of one LinkedIn category are one answer: their votes are pooled.

Grades for every label the published providers returned ship in gt/industry_grades_*.jsonl, so re-scoring is offline.
A label nobody has graded yet is left out of the score and listed; `company-enrichment-report --judge` grades it (paid
API calls, about $0.02 per label) and caches the votes in runs/industry_votes_local.jsonl.
"""
import hashlib
import json
import os
import re
import statistics
import urllib.request
from pathlib import Path

from company_enrichment.scorer.normalize import norm_label

GT = Path(__file__).resolve().parents[1] / "gt"
TAXONOMY = json.loads((GT / "linkedin_industries.json").read_text())
JUDGES = {  # name -> (route, model): Claude through the Anthropic API, the others through the Vercel AI Gateway
    "opus": ("anthropic", "claude-opus-5-5"),
    "sonnet": ("anthropic", "claude-sonnet-5-5"),
    "gemini": ("gateway", "google/gemini-3.1-pro-preview"),
    "sol": ("gateway", "openai/gpt-6.1-sol"),
    "astra": ("gateway", "openai/gpt-6-astra"),
}
SYSTEM = """You grade one industry label that a data vendor assigned to a company. You see the company's own description
of its business (from its annual report or official filings) and the label's official definition from LinkedIn's
industry taxonomy. You do not know which vendor produced the label.

Score the label:
- "1": the label's definition covers the company's main business (its largest source of revenue or its stated main
  activity), and no clearly more specific label would fit better.
- "0.5": the label is in the right area but too broad (a parent category of the best fit), or it correctly describes
  only a secondary part of the business (a segment of at least about 10% of revenue).
- "0": the label does not describe the company's main business or any significant segment.

Judge by the definition, not by how the label sounds. Write a short rationale (at most 60 words) first, then the score."""
SCHEMA = {"type": "object", "properties": {"rationale": {"type": "string"}, "score": {"type": "string", "enum": ["1", "0.5", "0"]}},
          "required": ["rationale", "score"], "additionalProperties": False}
JSON_ONLY = '\n\nAnswer with JSON only: {"rationale": "<at most 60 words>", "score": "1" | "0.5" | "0"}'
PROMPT_SHA = hashlib.sha256(json.dumps([SYSTEM, SCHEMA], sort_keys=True).encode()).hexdigest()


def label_id(label):
    return TAXONOMY["by_label"].get(norm_label(label)) if label else None


def pair_key(cid, label):
    return f"{cid}||{label.strip().lower()}"


def median_grade(scores):
    """Median of the votes cast, rounded to the nearest grade (1, 0.5, 0); None if no judge answered."""
    s = [x for x in scores if x is not None]
    return None if not s else min((1.0, 0.5, 0.0), key=lambda g: abs(g - statistics.median(s)))


def load_grades(paths):
    """Vote files (jsonl rows {company_id, label, votes: {judge: 1 | 0.5 | 0 | null}}) -> {pair key: final grade}.
    A label is final once every judge has voted (a refusal is a vote cast; the median skips it)."""
    votes = {}
    for p in paths:
        p = Path(p)
        if not p.exists(): continue
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            votes.setdefault(pair_key(r["company_id"], r["label"]), {}).update(r["votes"])
    full = {k: [v[j] for j in JUDGES] for k, v in votes.items() if set(v) >= set(JUDGES)}
    final = {k: g for k, vs in full.items() if (g := median_grade(vs)) is not None}
    pooled = {}  # one LinkedIn category under its old and new name: one answer, graded on the pooled votes
    for k, vs in full.items():
        cid, label = k.split("||", 1)
        lid = label_id(label)
        if lid: pooled.setdefault(f"{cid}||#{lid}", []).extend(vs)
    final.update({k: g for k, vs in pooled.items() if (g := median_grade(vs)) is not None})
    return final


def score(R, K, final):
    """-> (score or None when the label still needs judges, note)."""
    lid = label_id(R["industry"])
    ind = K["industry"]
    if lid and lid in ind["best_fit"]: s, note = 1.0, "pre-registered"
    elif lid and lid in ind["broad_ok"]: s, note = 0.5, "pre-registered"
    else:
        g = final.get(f"{K['id']}||#{lid}") if lid else None
        if g is None: g = final.get(pair_key(K["id"], R["industry"]))
        if g is None: return None, "needs judges"
        s, note = float(g), "judges"
    if lid and lid in ind["either_best_fit"]: return 1.0, note
    return (0.75 if s == 0.5 else s), note


def item(K, label):
    """What the judges see for one (company, label) pair."""
    lid = label_id(label)
    d = TAXONOMY["ids"].get(lid, {}) if lid else {}
    return {"id": pair_key(K["id"], label), "company_id": K["id"], "company": K["name"], "description": K["industry"]["description"],
            "label": label, "label_definition": d.get("description"), "label_hierarchy": d.get("hierarchy")}


def user_text(it):
    return (f"Company: {it['company']}\n\nWhat the company does (its own words):\n{it['description']}\n\n"
            f"Label: {it['label']}\nLabel definition: {it.get('label_definition') or 'none published'}\n"
            f"Label hierarchy: {it.get('label_hierarchy') or 'none'}")


def _ask_anthropic(model, it):
    import anthropic  # optional dependency: pip install ".[judges]"
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(model=model, max_tokens=4000, system=SYSTEM, output_config={
        "effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": user_text(it)}])
    if resp.stop_reason == "refusal": raise ValueError("refusal")
    return json.loads("".join(b.text for b in resp.content if b.type == "text"))


def _ask_gateway(model, it):
    body = {"model": model, "max_tokens": 4000,
            "messages": [{"role": "system", "content": SYSTEM + JSON_ONLY}, {"role": "user", "content": user_text(it)}]}
    req = urllib.request.Request("https://ai-gateway.vercel.sh/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + os.environ["AI_GATEWAY_API_KEY"], "Content-Type": "application/json"})
    text = json.loads(urllib.request.urlopen(req, timeout=300).read())["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


def judge(items, out_path):
    """Grade items with all five judges (three tries each; a refusal or bad answer is a missing vote) and append the
    votes to out_path. Paid: needs ANTHROPIC_API_KEY and AI_GATEWAY_API_KEY."""
    missing = [k for k in ("ANTHROPIC_API_KEY", "AI_GATEWAY_API_KEY") if not os.environ.get(k)]
    if missing: raise SystemExit(f"--judge needs {', '.join(missing)} (see .env.example)")
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as fh:
        for it in items:
            votes = {}
            for name, (route, model) in JUDGES.items():
                votes[name] = None
                for _ in range(3):
                    try:
                        ans = (_ask_anthropic if route == "anthropic" else _ask_gateway)(model, it)
                        if str(ans.get("score")) in ("1", "0.5", "0"): votes[name] = float(ans["score"]); break
                    except Exception as e:  # recorded as a missing vote after three tries
                        print(f"  {name} {it['id']}: {e!r}"[:160])
            fh.write(json.dumps({"company_id": it["company_id"], "label": it["label"].strip().lower(), "votes": votes,
                                 "prompt_sha256": PROMPT_SHA}) + "\n")
            print(f"  judged {it['id']}: {votes}")
