#!/usr/bin/env bash
set -euo pipefail

MODEL="ai/qwen3-embedding:4B"
DMR_HOST="http://localhost:12434"

echo "Checking Docker Model Runner..."
if ! curl -fsS "${DMR_HOST}/engines/v1/models" >/dev/null 2>&1; then
  echo "Docker Model Runner is not reachable at ${DMR_HOST}." >&2
  echo "Enable it with:" >&2
  echo "  docker desktop enable model-runner --tcp 12434" >&2
  exit 1
fi
echo "Docker Model Runner: OK"

echo "Checking for model ${MODEL}..."
if docker model list 2>/dev/null | grep -Fq "${MODEL}"; then
  echo "Model ${MODEL} is already pulled."
else
  echo "Pulling model ${MODEL} ..."
  docker model pull "${MODEL}"
fi

echo "Smoke-testing the embeddings endpoint..."
RESPONSE=$(curl -fsS "${DMR_HOST}/engines/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"ai/qwen3-embedding\",\"input\":\"test\"}" || true)

if echo "${RESPONSE}" | grep -q '"embedding"'; then
  echo "Embeddings endpoint: OK"
else
  echo "Warning: embeddings response did not contain an 'embedding' field." >&2
  echo "${RESPONSE}" >&2
fi

echo "Done."
