# Hybrid Search E2E Results

Generated: 2026-06-12 20:31:07 UTC

## Judge Report: AGENT-GREP BASELINE — how does user authentication work

**Score:** 85.1/100  |  **Confidence:** high  |  **Winner:** semantic_only

| Subscore | Value |
| --- | --- |
| semantic_relevance | 95 |
| kg_expansion_value | N/A |
| coverage | 90 |
| groundedness | 95 |
| noise_control | 96 |

**Strengths:**
- Semantic hits directly capture the core NextAuth configuration, server‑side helpers, tRPC context integration, and client‑side SessionProvider.
- Coverage spans the full authentication flow: configuration, session creation, guarded procedures, and React component usage.
- The evidence is tightly focused with minimal irrelevant chunks, yielding excellent signal‑to‑noise ratio.
- All agent claims are clearly backed by the retrieved snippets.

**Weaknesses:**
- No graph expansion occurred (semantic saturation), so cross‑dependency tracing (e.g., which routes actually use the authedProcedure) is missing, but this is expected given the query.
- The answer could benefit from a snippet showing the complete authOptions (callbacks, adapter, etc.), but the selected lines are sufficient for a high‑level understanding.

> Semantic retrieval scored very high (95) because every hit directly explains a part of the authentication flow. The knowledge‑graph expansion produced no new hits, indicating semantic saturation—the vector index already covered the entire relevant neighborhood. Therefore, kg_expansion_value is set to null (not applicable). Coverage is strong (90) across the auth config, tRPC integration, and client‑side providers; the only minor gaps are concrete protected‑route examples and env‑var details. Groundedness is excellent (95) as the agent analysis strictly paraphrases the retrieved snippets without unsupported claims. Noise control is very good (96) because all 10 hits are on‑topic and distinct. Using the semantic‑only weighting (kg_expansion placeholder 50), the final score is 85.1. The winner is semantic_only.

## Judge Report: HYBRID SEARCH — how does user authentication work

**Score:** 85.5/100  |  **Confidence:** high  |  **Winner:** semantic_only

| Subscore | Value |
| --- | --- |
| semantic_relevance | 95 |
| kg_expansion_value | N/A |
| coverage | 90 |
| groundedness | 95 |
| noise_control | 100 |

**Strengths:**
- Semantic hits are highly relevant and cover auth configuration, route setup, context creation, and authorization guard.
- No irrelevant hits; all retrieved snippets directly relate to authentication.
- Good coverage of the core authentication flow including provider configuration and integration with tRPC.

**Weaknesses:**
- Graph expansion provided no additional hits (semantic saturation), but that is not a weakness given vector search sufficiency.
- Some details such as session handling, token refresh, or sign-out flow are not directly present.

> Semantic search returned highly relevant evidence covering the core authentication setup (next-auth), configuration, providers, API route, context injection, and authorization guard. Graph expansion provided no additional hits, indicating semantic saturation where vector search already covered the relevant neighborhood. This is not a failure. The evidence is focused and sufficient to answer how authentication works, albeit missing some detailed session management. Agent analysis is entirely grounded. Noise is minimal. Overall strong performance.

