# Hybrid Search E2E Results

Generated: 2026-06-12 22:17:04 UTC

## Judge Report: AGENT-GREP BASELINE — how does user authentication work

**Score:** 76.0/100  |  **Confidence:** medium  |  **Winner:** semantic_only

| Subscore | Value |
| --- | --- |
| semantic_relevance | 95 |
| kg_expansion_value | N/A |
| coverage | 60 |
| groundedness | 70 |
| noise_control | 65 |

**Strengths:**
- Semantic hits tightly cover the core auth configuration (NextAuth init), tRPC middleware guard, session injection into context, and client-side UI integration.
- The evidence presents a complete end-to-end picture: server config -> session creation -> authorization check -> UI consumption.

**Weaknesses:**
- Coverage is slightly shallow: the NextAuth provider configuration (e.g., GitHub, email) and authOptions details are not included, only the initialization line.
- Three hits from the same channel layout file (import, signOut call, signIn call) cause mild noise and redundancy.
- The agent analysis is largely a repetition of the why_retrieved notes rather than a deeper synthesis of how the pieces connect.

> Semantic relevance is excellent: all ten hits directly address the query, covering the core config file, context middleware, protected procedure, layout provider, and UI consumption. The graph expansion is empty (semantic saturation), so kg_expansion_value is set to null and the winner defaults to 'semantic_only' — this is not a penalty, as the semantic hits already capture the authentication flow comprehensively. Coverage is strong but slightly penalized because the provider configuration and any database adapter/persistence logic are missing from the evidence. Groundedness is moderate: the agent analysis is factually consistent with the snippets but adds no insight beyond what the retrieval notes already say, and the mention of 'providers' exceeds the direct evidence shown. Noise is acceptable but three hits from the same layout file (import, signOut, signIn) contribute minimal additive value and increase repetition.

## Judge Report: HYBRID SEARCH — how does user authentication work

**Score:** 84.1/100  |  **Confidence:** high  |  **Winner:** semantic_only

| Subscore | Value |
| --- | --- |
| semantic_relevance | 92 |
| kg_expansion_value | N/A |
| coverage | 92 |
| groundedness | 100 |
| noise_control | 85 |

**Strengths:**
- Semantic search returned highly relevant results covering auth configuration, providers, middleware, and context integration.
- Evidence provides a comprehensive view of how authentication is set up in the codebase.
- No irrelevant or off-topic context was introduced.
- Agent analysis is factual and directly references retrieved snippets.

**Weaknesses:**
- Multiple snippets from the same file (auth.tsx) create slight redundancy, though each shows a different aspect.
- No graph expansion, but semantic saturation made it unnecessary.

> Semantic hits are highly relevant, covering auth configuration (auth.tsx), middleware (trpc.ts), context (context.ts), routing (route.ts), and client setup (layout.tsx). Since graph expansion returned no additional hits, semantic saturation occurred—the vector index already covered the relevant neighborhood. This is not a failure of the KG but confirms semantic search was thorough. The merged evidence is complete enough to explain how user authentication works; the agent analysis is clean and grounded. The winner is semantic_only due to the strong initial retrieval and absence of incremental graph value.

