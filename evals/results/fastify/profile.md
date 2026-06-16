# Fastify: hybrid vs grep agent eval

Generated: 2026-06-16 21:00:07 UTC  
Repo: `/Users/boris/Documents/dev/CodeKnow-worktrees/evals/evals/fastify-main`  
Judge: `deepseek-v4-pro` | seeds: 1 | items: 10

## Per-tool profile

| tool | grounding /5 | faithfulness /5 | consistency | preference win-rate | Wilson 95% CI | median tokens | median search calls | median wall (s) |
|---|---|---|---|---|---|---|---|---|
| hybrid | 4.7 | 4.6 | N/A | 70.0% | [0.40, 0.89] | 196914 | 4.5 | 30.6 |
| grep | 3.9 | 4.4 | N/A | 0.0% | [0.00, 0.28] | 41440 | 6.0 | 28.7 |

## Pairwise winners (Stage 2, double-swap)

| task | winner | confidence |
|---|---|---|
| fastify-01 | Tie | low |
| fastify-02 | hybrid | high |
| fastify-03 | hybrid | medium |
| fastify-04 | hybrid | medium |
| fastify-05 | hybrid | high |
| fastify-06 | Tie | low |
| fastify-07 | hybrid | high |
| fastify-08 | hybrid | high |
| fastify-09 | Tie | low |
| fastify-10 | hybrid | high |

## Per-task detail

### fastify-01 — Fastify answers HEAD requests for GET routes without sending the full GET body. Where is this HEAD-route behavior implemented, and how does it compute the Content-Length without serializing the payload?

**Pairwise winner:** Tie (low)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 4/5, faithfulness 4/5, existence 83%
  - ungrounded claims:
    - The implicit HEAD route runs the GET handler normally (so the payload is serialized)
  - hallucinated paths:
    - route.js:455

### fastify-02 — Where is the default request id generated, and how can it be customized or read from an incoming header?

**Pairwise winner:** hybrid (high)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 4/5, faithfulness 5/5, existence 100%
  - ungrounded claims:
    - The claim that setting requestIdHeader to true uses the default header name 'request-id' is not shown in the cited test code.

### fastify-03 — How does a request with a JSON body move through content-type parsing, validation, hooks, and the user handler?

**Pairwise winner:** hybrid (medium)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 5/5, faithfulness 5/5, existence 100%

### fastify-04 — How is the Request object constructed from the raw Node req, and how does the trustProxy option change which headers are trusted for protocol, host, and ip detection?

**Pairwise winner:** hybrid (medium)

- **hybrid:** grounding 4/5, faithfulness 3/5, existence 100%
  - ungrounded claims:
    - Claims that there is no protocol getter on the base Request prototype, and that it falls through to undefined or generic http. The cited code at lib/request.js:244 shows a protocol getter defined on Request.prototype that returns 'https' or 'http' based on socket.encrypted.
    - Mention that the actual new context.Request(...) call happens in lib/handle-request.js, but that file is not among the cited code, so the claim cannot be verified.
- **grep:** grounding 4/5, faithfulness 4/5, existence 100%
  - ungrounded claims:
    - When trustProxy is falsy, these properties fall back to the raw Node.js req connection values.

### fastify-05 — How are route schemas normalized, compiled for validation and serialization, and attached to a route context?

**Pairwise winner:** hybrid (high)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 4/5, faithfulness 4/5, existence 100%
  - ungrounded claims:
    - the exact attachment point is in lib/route.js and lib/validation.js

### fastify-06 — How are errors from hooks or handlers converted into HTTP responses?

**Pairwise winner:** Tie (low)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 5/5, faithfulness 5/5, existence 57%
  - hallucinated paths:
    - handle-request.js:93
    - reply.js:150
    - error-handler.js:30
    - error-handler.js:83
    - error-handler.js:153
    - error-handler.js:104

### fastify-07 — How does Fastify start listening on localhost, and why can it bind multiple local addresses?

**Pairwise winner:** hybrid (high)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 2/5, faithfulness 5/5, existence 100%
  - ungrounded claims:
    - Claim that dns.lookup(...) appears at lib/server.js:156: the cited line shows unrelated code from the listen function, not the dns.lookup call.
    - Claim that lib/server.js:169-172 obtains the main server's address and compares it: those lines are part of the listen function's promise handling, not the comparison logic.
    - Claim that lib/server.js:173-175 creates secondary servers: those lines are the end of the listen function and do not contain the described secondary server instantiation.
    - Claim that lib/server.js:180 stores secondary servers in this[kServerBindings]: the cited line is within the forceCloseConnections assignment, not the push operation.
    - Claim that lib/server.js:190 and 211 relate to secondary server creation: the exact lines are not verifiable as the described code in the provided snippets.

### fastify-08 — Where are decorators implemented, and how does Fastify check decorator dependencies and prevent duplicate decorations?

**Pairwise winner:** hybrid (high)

- **hybrid:** grounding 4/5, faithfulness 5/5, existence 100%
  - ungrounded claims:
    - Claim that decorateFastify is at lib/decorate.js:75-79 (actual location is around 119-124)
    - Claim that decorateRequest is at lib/decorate.js:126-131 (actual location is later in the file)
    - Claim that decorateReply is at lib/decorate.js:119-124 (actual location is later in the file)
- **grep:** grounding 3/5, faithfulness 3/5, existence 100%
  - ungrounded claims:
    - Plugin-level decorator/dependency validation happens in lib/plugin-utils.js (checkDecorators at line 80, checkDependencies at line 65).
    - In lib/plugin-utils.js, the registerPlugin() function (line 147-153) calls checkDecorators() (line 80-90) to verify decorators and throws FST_ERR_PLUGIN_NOT_PRESENT_IN_INSTANCE if missing.
    - In lib/plugin-utils.js, checkDependencies() (line 65-78) reads plugin's meta.dependencies and verifies each dependency plugin has been registered, throwing FST_ERR_PLUGIN_DEPENDENCY_NOT_REGISTERED if missing.

### fastify-09 — How does Fastify handle 404 routes, including encapsulated not-found handlers and route prefixes?

**Pairwise winner:** Tie (low)

- **hybrid:** grounding 5/5, faithfulness 5/5, existence 100%
- **grep:** grounding 4/5, faithfulness 4/5, existence 100%
  - ungrounded claims:
    - The agent claims that arrange404 is called every time a plugin is registered, but the code shows it is only called when a prefix is provided (inside an if (opts.prefix) block).

### fastify-10 — Which file implements Express middleware support in core Fastify? If core does not implement it, cite the code that says what to do instead.

**Pairwise winner:** hybrid (high)

- **hybrid:** grounding 4/5, faithfulness 3/5, existence 100%
  - ungrounded claims:
    - The test/middleware.test.js:9-14 shows that you should register @fastify/middie or @fastify/express
- **grep:** grounding 4/5, faithfulness 5/5, existence 100%
  - ungrounded claims:
    - The core library has no .use method (confirmed by searching /lib for .use — zero matches) and no middleware implementation.

## Bias & significance

- length↔win correlation: 0.15 (flagged: False)
- stats: significance tests deferred (scipy not yet a dependency)

