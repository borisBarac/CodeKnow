"""Prompt templates for the LLM judge stages.

Verbatim intent from JUDGE_PRINCIPLES section 6. The judge never sees a gold
answer and must not invent one (reference-free, section 3). All templates are
``str.format``-able.
"""

from __future__ import annotations

STAGE1_PROMPT = """\
You are checking whether an agent's answer is SUPPORTED BY the code it cited.
You do NOT know the correct answer and must NOT invent one. Only decide if the
cited evidence actually backs the claim.

<TASK>{task_prompt}</TASK>
<AGENT_ANSWER>{final_answer}</AGENT_ANSWER>
<CITED_CODE>{cited_code}</CITED_CODE>

Score two dimensions 0-5:
- grounding: do the cited locations exist AND does the code there support the
  specific claim? 0=fabricated/nonexistent, 5=every claim directly backed.
- faithfulness: does the answer stay within what the cited evidence shows
  (no overclaim, no extrapolation)? 0=contradicts evidence, 5=fully contained.

- hallucinated_paths: ONLY cited paths marked [FILE NOT FOUND] above (the file
  does not exist). Do NOT list paths whose code you did see — if a cited range
  was incomplete or not shown, raise that under ungrounded_claims instead.

Return JSON only:
{{
  "grounding": <int 0-5>,
  "faithfulness": <int 0-5>,
  "ungrounded_claims": ["..."],
  "hallucinated_paths": ["..."]
}}
"""


STAGE2_PROMPT = """\
Two agents with different search tools did the same task. You do NOT know the
correct answer. Do not invent one. Judge which answer is BETTER GROUNDED and
MORE COMPLETE relative to the task — not which "sounds more correct."
Ignore narration style, confidence, and verbosity.

<TASK>{task_prompt}</TASK>
Candidate 1 answer: {answer_1}
Candidate 1 cited code: {code_1}
Candidate 2 answer: {answer_2}
Candidate 2 cited code: {code_2}

Criteria: (1) Grounding — claims backed by cited code; (2) Completeness — all
parts of the task addressed; (3) Precision — no irrelevant tangents.
Think step by step, then return JSON only:
{{
  "reasoning": "...",
  "winner": "Candidate 1" | "Candidate 2" | "Tie",
  "confidence": "high" | "medium" | "low"
}}
"""


CONSISTENCY_PROMPT = """\
Same task, two runs by the same tool. Are these answers semantically equivalent
(same key claims, same cited locations)? You do NOT know the correct answer.

<TASK>{task_prompt}</TASK>
Answer 1: {answer_1}
Answer 2: {answer_2}

Return JSON only:
{{
  "equivalent": <true|false>,
  "agreement_score": <0.0-1.0>
}}
"""
