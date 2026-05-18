# ruff: noqa: T201
# mypy: disable-error-code="no-untyped-def"
"""E2E test for the LLM judge.

Tests the judge itself — not the search pipeline. Constructs synthetic
JudgeInput data and validates that the judge returns well-formed output
for different input scenarios.

Requires:
  - Judge LLM API key (JUDGE_LLM_API_KEY or OPENROUTER_API_KEY)
"""

from __future__ import annotations

import json
import os

import pytest
from codeknow.schemas import HybridSearchResult
from dotenv import load_dotenv

from judge import JudgeOutput, LLMJudge, from_hybrid_response
from judge.schemas import JudgeHit, JudgeInput

load_dotenv()

# ── Fail fast if no API key ──────────────────────────────────────────
_api_key = os.environ.get("JUDGE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
if not _api_key:
    pytest.exit(
        "JUDGE_LLM_API_KEY or OPENROUTER_API_KEY is required for judge tests",
        returncode=1,
    )


# ── Synthetic data helpers ────────────────────────────────────────────
REPO_BRIEF = (
    "tRPC + SSE chat app built with Next.js. "
    "Features: server-sent events subscriptions, Drizzle ORM, next-auth. "
    "Key dirs: src/server/routers/, src/app/channels/."
)


def _hit(
    hit_id: str,
    file_path: str = "src/server/routers/channel.ts",
    snippet: str = "export function createChannel() { ... }",
    kind: str = "vector",
    symbol: str | None = None,
    score: float | None = None,
    relation_to_seed: str | None = None,
    why_retrieved: str | None = None,
    relation_type: str | None = None,
    relation_weight: float | None = None,
    cumulative_weight: float | None = None,
) -> JudgeHit:
    return JudgeHit(
        id=hit_id,
        file_path=file_path,
        symbol=symbol,
        kind=kind,
        snippet=snippet,
        score=score,
        relation_to_seed=relation_to_seed,
        why_retrieved=why_retrieved or ("semantic match" if kind == "vector" else None),
        relation_type=relation_type,
        relation_weight=relation_weight,
        cumulative_weight=cumulative_weight,
    )


def _make_input(
    query: str = "how does the channel subscription work",
    semantic_hits: list[JudgeHit] | None = None,
    graph_hits: list[JudgeHit] | None = None,
    agent_analysis: str = "",
) -> JudgeInput:
    if semantic_hits is None:
        semantic_hits = [
            _hit("s1", snippet="export const channelRouter = router({ ... })"),
            _hit(
                "s2",
                file_path="src/app/channels/[channelId]/hooks.ts",
                snippet="trpc.channel.onPost.useSubscription({ ... })",
            ),
            _hit(
                "s3",
                file_path="src/server/routers/post.ts",
                snippet="export const postRouter = router({ ... })",
            ),
        ]
    if graph_hits is None:
        graph_hits = [
            _hit(
                "g1",
                kind="graph",
                file_path="src/server/context.ts",
                snippet="export function createContext() { ... }",
                relation_to_seed="channelRouter →calls→ createContext",
                why_retrieved="graph expansion via channelRouter →calls→ createContext",
                relation_type="calls",
                relation_weight=0.7,
                cumulative_weight=0.7,
            ),
            _hit(
                "g2",
                kind="graph",
                file_path="src/server/routers/post.ts",
                snippet="export async function onPostAdded() { ... }",
                relation_to_seed="channelRouter →calls→ postRouter.onPostAdded",
                why_retrieved=(
                    "graph expansion via channelRouter →calls→ postRouter.onPostAdded"
                ),
                relation_type="calls",
                relation_weight=0.7,
                cumulative_weight=0.7,
            ),
        ]
    merged = list(semantic_hits) + list(graph_hits)
    return JudgeInput(
        query=query,
        repo_brief=REPO_BRIEF,
        semantic_hits=list(semantic_hits),
        graph_hits=list(graph_hits),
        merged_hits=merged,
        agent_analysis=agent_analysis,
    )


def _print_report(output: JudgeOutput, label: str) -> None:
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"TEST: {label}")
    print(f"FINAL SCORE: {output.final_score:.1f}/100")
    print(f"CONFIDENCE:  {output.confidence}")
    print(f"WINNER:      {output.winner}")
    print("\nSUBSCORES:")
    for field, val in output.subscores.model_dump().items():
        display = "N/A" if val is None else f"{val:3d}"
        print(f"  {field:25s} {display}")
    print("\nSTRENGTHS:")
    for s in output.strengths:
        print(f"  + {s}")
    print("\nWEAKNESSES:")
    for w in output.weaknesses:
        print(f"  - {w}")
    if output.unsupported_claims:
        print("\nUNSUPPORTED CLAIMS:")
        for uc in output.unsupported_claims:
            print(f"  ! {uc.claim}")
            print(f"    Reason: {uc.reason}")
    if output.missing_evidence:
        print("\nMISSING EVIDENCE:")
        for me in output.missing_evidence:
            print(f"  ? {me}")
    print(f"\nRATIONALE:\n  {output.rationale}")
    print("\nEVIDENCE USED:")
    eu = output.evidence_used
    print(f"  semantic: {len(eu.semantic_hit_ids)} hits")
    print(f"  graph:    {len(eu.graph_hit_ids)} hits")
    print(f"  merged:   {len(eu.merged_hit_ids)} hits")
    print(sep)


def _assert_valid_output(output: JudgeOutput) -> None:
    assert isinstance(output, JudgeOutput)
    assert 0.0 <= output.final_score <= 100.0
    assert output.confidence in ("low", "medium", "high")
    assert output.winner in ("semantic_only", "hybrid", "tie", "unknown")
    assert isinstance(output.rationale, str)
    assert len(output.rationale) > 0
    assert isinstance(output.strengths, list)
    assert isinstance(output.weaknesses, list)
    assert isinstance(output.unsupported_claims, list)
    assert isinstance(output.missing_evidence, list)
    for field, val in output.subscores.model_dump().items():
        if val is None:
            continue
        assert 0 <= val <= 100, f"{field}={val} not in [0, 100]"


# ── Tests ──────────────────────────────────────────────────────────────


def test_judge_returns_valid_output():
    judge = LLMJudge()
    judge_input = _make_input()
    output = judge.judge(judge_input)
    _print_report(output, "happy path — 3 semantic + 2 graph hits")
    _assert_valid_output(output)


def test_judge_semantic_only():
    judge = LLMJudge()
    judge_input = _make_input(
        query="how does authentication work",
        semantic_hits=[
            _hit("s1", snippet="export const authOptions = { ... }"),
            _hit(
                "s2",
                file_path="src/app/api/auth/[...nextauth].ts",
                snippet="export default NextAuth(authOptions)",
            ),
            _hit(
                "s3",
                file_path="src/server/context.ts",
                snippet="export async function getSession() { ... }",
            ),
            _hit("s4", snippet="const session = await getSession(req)"),
            _hit("s5", snippet="if (!session) throw new TRPCError(...)"),
        ],
        graph_hits=[],
    )
    output = judge.judge(judge_input)
    _print_report(output, "semantic only — 5 semantic, 0 graph")
    _assert_valid_output(output)
    assert (
        output.subscores.kg_expansion_value is None
        or output.subscores.kg_expansion_value < 20
    )


def test_judge_with_agent_analysis():
    judge = LLMJudge()
    judge_input = _make_input(
        agent_analysis=(
            "The channel subscription uses SSE through tRPC's httpSubscriptionLink. "
            "The channelRouter exposes an onPost subscription that pushes new posts "
            "in real-time. The hooks in hooks.ts consume "
            "this subscription on the client. "
            "The system also uses Redis for pub/sub event distribution, though Redis "
            "is not shown in the retrieved evidence."
        ),
    )
    output = judge.judge(judge_input)
    _print_report(output, "with agent analysis")
    _assert_valid_output(output)


def test_judge_output_serializable():
    judge = LLMJudge()
    judge_input = _make_input(query="serialization test")
    output = judge.judge(judge_input)
    data = output.model_dump()
    serialized = json.dumps(data, default=str)
    reloaded = JudgeOutput.model_validate_json(serialized)
    assert reloaded.final_score == output.final_score
    assert reloaded.confidence == output.confidence
    assert reloaded.winner == output.winner
    assert reloaded.subscores.model_dump() == output.subscores.model_dump()


def test_from_hybrid_response_converter():
    results = [
        HybridSearchResult(
            chunk_hash="a" * 64,
            file="src/server/routers/channel.ts",
            start_line=1,
            end_line=20,
            content="export const channelRouter = router({});",
            distance=0.15,
            node_labels=["channelRouter"],
            provenance="vector",
        ),
        HybridSearchResult(
            chunk_hash="b" * 64,
            file="src/server/context.ts",
            start_line=1,
            end_line=10,
            content="export function createContext() {}",
            provenance="graph",
            graph_path=["channelRouter", "→calls→", "createContext"],
            node_labels=["createContext"],
            cumulative_weight=0.7,
        ),
        HybridSearchResult(
            chunk_hash="c" * 64,
            file="src/app/hooks.ts",
            start_line=5,
            end_line=15,
            content="trpc.channel.onPost.useSubscription({});",
            distance=0.22,
            node_labels=["useSubscription"],
            provenance="vector",
        ),
    ]
    from codeknow.schemas import HybridSearchResponse

    response = HybridSearchResponse(
        query="channel subscription",
        vector_hits=2,
        graph_expanded=1,
        results=results,
    )

    judge_input = from_hybrid_response(response, repo_brief="test repo")

    assert judge_input.query == "channel subscription"
    assert len(judge_input.semantic_hits) == 2
    assert len(judge_input.graph_hits) == 1
    assert len(judge_input.merged_hits) == 3

    semantic_ids = {h.id for h in judge_input.semantic_hits}
    graph_ids = {h.id for h in judge_input.graph_hits}
    assert semantic_ids == {"a" * 64, "c" * 64}
    assert graph_ids == {"b" * 64}

    graph_hit = judge_input.graph_hits[0]
    assert graph_hit.kind == "graph"
    assert graph_hit.relation_to_seed is not None
    assert "createContext" in graph_hit.relation_to_seed
    assert graph_hit.relation_type == "calls"
    assert graph_hit.relation_weight == 0.7
    assert graph_hit.cumulative_weight == 0.7

    semantic_hit = judge_input.semantic_hits[0]
    assert semantic_hit.why_retrieved == "semantic match"
    assert semantic_hit.relation_type is None
    assert semantic_hit.relation_weight is None
    assert semantic_hit.cumulative_weight is None


def test_judge_semantic_saturation():
    judge = LLMJudge()
    judge_input = _make_input(
        query="how does authentication work",
        semantic_hits=[
            _hit("s1", snippet="export const authOptions = { ... }"),
            _hit(
                "s2",
                file_path="src/app/api/auth/[...nextauth].ts",
                snippet="export default NextAuth(authOptions)",
            ),
            _hit(
                "s3",
                file_path="src/server/context.ts",
                snippet="export async function getSession() { ... }",
            ),
            _hit("s4", snippet="const session = await getSession(req)"),
            _hit("s5", snippet="if (!session) throw new TRPCError(...)"),
        ],
        graph_hits=[],
    )
    output = judge.judge(judge_input)
    _print_report(output, "semantic saturation — 5 strong semantic, 0 graph")
    _assert_valid_output(output)
    assert output.subscores.kg_expansion_value is None
    assert output.winner == "semantic_only"
    assert output.final_score >= 70
    assert output.subscores.coverage >= 60
    assert output.subscores.noise_control >= 60
    assert any(
        "saturation" in s.lower() or "no graph" in s.lower() or "semantic" in s.lower()
        for s in output.strengths
    )


def test_judge_high_value_graph_expansion():
    judge = LLMJudge()
    judge_input = _make_input(
        query="how does the SSE subscription flow work end-to-end",
        semantic_hits=[
            _hit(
                "s1",
                snippet="export const channelRouter = router({ onPost: subscription })",
            ),
            _hit(
                "s2",
                file_path="src/app/channels/[channelId]/hooks.ts",
                snippet="trpc.channel.onPost.useSubscription({ ... })",
            ),
        ],
        graph_hits=[
            _hit(
                "g1",
                kind="graph",
                file_path="src/server/sse.ts",
                snippet="export class SSEEmitter { stream() { ... } }",
                relation_to_seed=("channelRouter →semantically_similar_to→ SSEEmitter"),
                why_retrieved=(
                    "graph expansion via"
                    " channelRouter →semantically_similar_to→ SSEEmitter"
                ),
                relation_type="semantically_similar_to",
                relation_weight=1.0,
                cumulative_weight=1.0,
            ),
            _hit(
                "g2",
                kind="graph",
                file_path="src/server/routers/post.ts",
                snippet="export async function onPostAdded() { ... }",
                relation_to_seed=("channelRouter →rationale_for→ onPostAdded"),
                why_retrieved=(
                    "graph expansion via channelRouter →rationale_for→ onPostAdded"
                ),
                relation_type="rationale_for",
                relation_weight=0.9,
                cumulative_weight=0.9,
            ),
        ],
    )
    output = judge.judge(judge_input)
    _print_report(output, "high-value graph expansion (1.0 + 0.9 weights)")
    _assert_valid_output(output)
    assert output.subscores.kg_expansion_value is not None
    assert output.subscores.kg_expansion_value >= 60
    assert output.winner == "hybrid"


def test_judge_low_value_graph_expansion():
    judge = LLMJudge()
    judge_input = _make_input(
        query="how does authentication work",
        semantic_hits=[
            _hit("s1", snippet="export const authOptions = { ... }"),
            _hit(
                "s2",
                file_path="src/app/api/auth/[...nextauth].ts",
                snippet="export default NextAuth(authOptions)",
            ),
        ],
        graph_hits=[
            _hit(
                "g1",
                kind="graph",
                file_path="src/utils/logger.ts",
                snippet="export function log(message: string) { ... }",
                relation_to_seed="authOptions →calls→ logger.log",
                why_retrieved="graph expansion via authOptions →calls→ logger.log",
                relation_type="calls",
                relation_weight=0.7,
                cumulative_weight=0.7,
            ),
        ],
    )
    output = judge.judge(judge_input)
    _print_report(output, "low-value graph expansion (tangential calls edge)")
    _assert_valid_output(output)
    assert output.subscores.kg_expansion_value is not None
    assert output.subscores.kg_expansion_value < 50
    assert output.winner in ("semantic_only", "tie")
