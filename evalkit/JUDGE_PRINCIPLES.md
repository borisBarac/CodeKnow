# Judge Principles

Spec for how the `judge` module (consumed by `evals/eval_word_press.py` via
`from judge import JudgeOutput, LLMJudge, calculate_final_score, ...`) should
evaluate agentic search.

## 1. Goal & scope

We compare **two agents using the same model but different search tools** over a
repository. Each agent attempts the same task; we measure which tool enables the
agent to search better, scored on **result quality + cost (time/tokens)**.

This is an **agentic eval**: the unit of evaluation is an *agent run*
(task -> trajectory -> outcome), not a static search-result list.

We optimize for **grounding, faithfulness, consistency, and preference**.
We do **not** optimize for correctness, and we assume **no gold answers**.

## 2. The four optimization targets

| Dimension | Operational definition | How measured | Scale |
|---|---|---|---|
| **Grounding** | Claims are tied to *real* code at the cited locations. | Deterministic: citation existence rate (`%` of cited `file:line` that exist). LLM: does the code at that location actually support the specific claim? | `0-5` + existence `%` |
| **Faithfulness** | Answer follows from the *collected* evidence — no overclaim beyond what was cited, no fabrication. | LLM: `%` of answer claims supported by cited snippets; flag hallucinated claims/paths. | `0-5` |
| **Consistency** | The tool makes the agent *reproducible* — same task, multiple seeds, convergent answers. | Inter-seed: semantic agreement of final answers + Jaccard of citation sets, across `N` seeds. | agreement `%` |
| **Preference** | Head-to-head, which run is preferred on grounding + completeness + precision. | Pairwise LLM judge, double-swapped (`AB` + `BA`; disagree -> `Tie`). | win-rate `%` + CI |

Grounding and faithfulness are separate because they fail differently:
- An agent can cite *real* code yet *overclaim* what it shows -> grounded but unfaithful.
- An agent can stay faithful to its evidence while that evidence is misread -> faithful but ungrounded.

## 3. The honest ceiling (reference-free evaluation)

Without a gold answer, we **cannot confirm correctness** — only grounding,
faithfulness, consistency, and preference. We can catch the dominant failure
mode (confident fabrication, ungrounded claims) but we cannot catch
"confident, grounded, and wrong" when both agents are stably wrong together.
Every number in this benchmark is read with that limit in mind.

## 4. Judge architecture (3-stage pipeline, cheap -> expensive)

Each stage maps to specific dimensions so nothing is double-scored.

- **Stage 0 — deterministic (no LLM):**
  - Verify every cited `file:line` exists in the repo -> existence rate.
  - Extract the real code snippet at each cited location (fed to Stages 1 and 2).
  - Compute citation-set Jaccard across seeds.
  - Compute cost: `tokens_in/out`, `search_calls`, `llm_turns`, `wall_clock_s`,
    `steps_to_first_relevant`.
- **Stage 1 — LLM, per-run (grounding + faithfulness together, shared context):**
  - One prompt scores both dimensions and emits `ungrounded_claims[]` and
    `hallucinated_paths[]`.
- **Stage 2 — LLM, pairwise, double-swapped (preference):**
  - Run only on cross-tool pairs; two orderings (`AB` and `BA`).
  - Verdict disagreement -> `Tie` + `confidence: low`.
- **Stage 3 — consistency:**
  - LLM-judge seed-pair semantic equivalence on a subset; embeddings cosine for
    the rest. -> agreement matrix -> consistency `%`.

## 5. Data contract

No `gold` field anywhere.

**Task:**
```json
{
  "task_id": "T-014",
  "type": "locate",            // locate | reasoning | aggregation | trap
  "stratum": "multi-hop",      // for slicing results
  "difficulty": "medium",
  "prompt": "Find where OAuth2 token refresh is implemented and name the function that retries on a 401 with a new token.",
  "trap": false                // true => correct answer is "not found"
}
```

**Run** (one per `task x tool x seed`):
```json
{
  "task_id": "T-014",
  "tool": "A",
  "seed": 3,
  "final_answer": "<agent's text answer>",
  "cited_locations": ["src/auth/oauth.py:144", "src/auth/refresh.py:12"],
  "cost": {
    "search_calls": 7, "llm_turns": 11,
    "tokens_in": 48230, "tokens_out": 3120,
    "wall_clock_s": 38.4, "steps_to_first_relevant": 4
  }
}
```

**Judgment** (per run):
```json
{
  "task_id": "T-014", "tool": "A", "seed": 3,
  "grounding": 4, "existence_rate": 1.0,
  "faithfulness": 3,
  "ungrounded_claims": ["claims retry is exponential; cited code shows fixed delay"],
  "hallucinated_paths": [],
  "consistency_vs_other_seeds": 0.67
}
```

**Pairwise judgment** (per task, between tools):
```json
{ "task_id": "T-014", "winner": "A", "confidence": "high" }
```

Field names align with the existing module surface: `JudgeOutput` carries the
per-run judgment; `calculate_final_score` aggregates into the report profile.

## 6. Prompt templates

### Stage 1 — grounding + faithfulness (per run, reference-free)

```text
You are checking whether an agent's answer is SUPPORTED BY the code it cited.
You do NOT know the correct answer and must NOT invent one. Only decide if the
cited evidence actually backs the claim.

<TASK>{task.prompt}</TASK>
<AGENT_ANSWER>{run.final_answer}</AGENT_ANSWER>
<CITED_CODE>{fetched snippets at run.cited_locations, with file:line headers}</CITED_CODE>

Score two dimensions 0-5:
- grounding: do the cited locations exist AND does the code there support the
  specific claim? 0=fabricated/nonexistent, 5=every claim directly backed.
- faithfulness: does the answer stay within what the cited evidence shows
  (no overclaim, no extrapolation)? 0=contradicts evidence, 5=fully contained.

{
  "grounding": int,
  "faithfulness": int,
  "ungrounded_claims": ["..."],
  "hallucinated_paths": ["..."]
}
```

### Stage 2 — preference (pairwise, double-swapped)

```text
Two agents with different search tools did the same task. You do NOT know the
correct answer. Do not invent one. Judge which answer is BETTER GROUNDED and
MORE COMPLETE relative to the task — not which "sounds more correct."
Ignore narration style, confidence, and verbosity.

<TASK>{task.prompt}</TASK>
Candidate 1 answer: {a.final_answer}
Candidate 1 cited code: {fetched snippets}
Candidate 2 answer: {b.final_answer}
Candidate 2 cited code: {fetched snippets}

Criteria: (1) Grounding — claims backed by cited code; (2) Completeness — all
parts of the task addressed; (3) Precision — no irrelevant tangents.
Think step by step, then:

{
  "reasoning": "...",
  "winner": "Candidate 1" | "Candidate 2" | "Tie",
  "confidence": "high" | "medium" | "low"
}
```

Run twice (swap candidate order). Disagreement -> `Tie`, `confidence: low`.

## 7. Aggregation

Do **not** collapse into one headline number. Report a per-tool, per-stratum
**profile**:

- grounding `/5` (mean)
- faithfulness `/5` (mean)
- consistency `%` (inter-seed agreement)
- preference win-rate `%` (Wilson CI)
- cost: median `tokens`, `search_calls`, `wall_clock_s`, `steps_to_first_relevant`

Then two views:
- **Pareto:** preference win-rate vs median tokens (or vs wall-clock).
- **Paired stats:** both tools run the same tasks, so use McNemar (preference
  pass/fail) and paired Wilcoxon (grounding/faithfulness scores) for
  significance, with 95% bootstrap CIs.

The profile is the decision tool. A headline blend hides the interesting
findings (e.g. "Tool A wins preference but costs 5x the tokens"; "Tool B is more
grounded but less consistent").

## 8. Calibration — trap tasks

Trap tasks ("find X" where X doesn't exist; correct behavior = "not found", no
fabricated citation) directly stress-test **grounding** and **faithfulness**.
~10-15 traps let us verify the grounding judge does not let fabricated citations
through. That check is the quality gate for the whole benchmark.

## 9. Anti-bias rules

- **Double-swap** for every pairwise judgment; discard disagreements as ties.
- **Judge the outcome, never the narration** — confident-sounding reasoning
  text must not raise the score (process bias).
- **No self-reference** — because agents and judge use the *same* model, never
  ask the judge to first produce its own "ideal answer" then grade against it;
  compare candidates directly. Self-reference only injects the judge's own
  errors.
- **Verbosity guard** — explicit instruction to ignore length; also
  length-normalize post-hoc (check correlation between answer length and win
  rate; `>0.3` => mitigation is failing).

## 10. Gap: current `LLMJudge` vs target

The current `LLMJudge` performs a single judge call per result. To reach this
spec it must move to the 3-stage pipeline:

- [ ] Stage 0 deterministic citation-existence check + snippet extraction.
- [ ] Stage 1 split scoring into `grounding` + `faithfulness` (currently a
      single blended score).
- [ ] Stage 2 pairwise double-swap with `confidence` field.
- [ ] Stage 3 consistency across seeds (requires `N >= 2` seeds per task/tool).
- [ ] `calculate_final_score` emits the 4-axis profile + cost (no single blend).
- [ ] Trap-task calibration set.

## 11. Open parameters (defaults)

| Parameter | Default | Rationale |
|---|---|---|
| Seeds per task per tool | `3` | enables consistency; balances cost (`2` minimum, `5` research-grade) |
| Consistency measurement | LLM-judge on a subset + embeddings cosine for the rest | accuracy where it matters, cost where it doesn't |
| Preference unit | all cross-tool seed pairs (`A_i` vs `B_j`) averaged | reuses seeds already run; more stable than a single representative run |
