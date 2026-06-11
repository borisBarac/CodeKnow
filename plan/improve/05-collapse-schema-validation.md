# Candidate 5: Collapse the schema validation duplication

**Strength:** Worth exploring
**Dependency category:** in-process

## Files involved

| File | Lines | Role |
|---|---|---|
| `codeknow/schemas.py` | 168 | Pydantic v2 data models for nodes, edges, chunks, search results |
| `codeknow/validate.py` | 140 | Raw dict-based validation against hardcoded constant lists |

### Also affected

| File | What it uses from validate.py |
|---|---|
| `codeknow/graph/build.py` | `validate_extraction()` — called during graph building |

## Current state of schemas.py

### Models

```python
class ConfidenceLabel(str, Enum):
    EXTRACTED = "extracted"    # deterministic AST extraction
    INFERRED = "inferred"      # LLM-inferred, lower confidence
    AMBIGUOUS = "ambiguous"    # uncertain, lowest confidence

class Chunk(BaseModel):
    hash: str
    file: str
    start_line: int
    end_line: int
    content: str = ""
    model_config = ConfigDict(extra="allow")

class ChunkRef(BaseModel):
    hash: str
    file: str
    start_line: int
    end_line: int

class Node(BaseModel):
    id: str
    label: str
    type: str
    file: str = ""
    source_location: str = ""
    extra: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")

class Edge(BaseModel):
    source: str
    target: str
    relation: str
    confidence: float = 1.0
    extra: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")

class ExtractionResult(BaseModel):
    nodes: list[Node]
    edges: list[Edge]

class FileDiscovery(BaseModel):
    code_files: list[str]
    document_files: list[str]
    sensitive_files: list[str]
    total_files: int
    total_words: int
    needs_graph: bool

class EmbedStats(BaseModel):
    chunks_embedded: int
    chunks_skipped: int
    elapsed_seconds: float

class HybridSearchResult(BaseModel):
    file: str
    start_line: int
    end_line: int
    content: str
    distance: float
    provenance: str           # "vector" | "graph" | "vector+graph"
    weight: float = 0.0
    graph_path: list[str] = Field(default_factory=list)
    node_labels: list[str] = Field(default_factory=list)
    community_ids: list[int] = Field(default_factory=list)
    slug: str = ""

class HybridSearchResponse(BaseModel):
    query: str
    vector_hits: int
    graph_expanded: int
    results: list[HybridSearchResult]

class RepoMetadata(BaseModel):
    slug: str
    url: str = ""
    commit_hash: str = ""
    node_count: int = 0
    edge_count: int = 0
    community_count: int = 0
    built_at: str = ""

class ListReposResponse(BaseModel):
    repos: list[RepoMetadata]
    errors: list[dict] = Field(default_factory=list)

type ChunkMap = dict[str, Chunk]
type CommunityMap = dict[int, list[str]]
```

## Current state of validate.py

### Duplicated constants

```python
VALID_FILE_TYPES = {"file", "class", "function", "method", "module", "variable"}
VALID_CONFIDENCES = {"extracted", "inferred", "ambiguous"}
REQUIRED_NODE_FIELDS = {"id", "label", "type", "file"}
REQUIRED_EDGE_FIELDS = {"source", "target", "relation"}
```

These mirror (but can drift from) the Pydantic models:
- `VALID_FILE_TYPES` — not enforced by `Node.type` in schemas.py (no validator)
- `VALID_CONFIDENCES` — mirrors `ConfidenceLabel` enum values, but is a separate set
- `REQUIRED_NODE_FIELDS` — mirrors `Node` model fields, but is a separate list
- `REQUIRED_EDGE_FIELDS` — mirrors `Edge` model fields, but is a separate list

### Functions

```python
def validate_extraction(data: dict) -> list[str]:
    """Returns list of error strings. Empty list = valid."""
    errors = []
    for node in data.get("nodes", []):
        errors.extend(_validate_node(node))
    for edge in data.get("edges", []):
        errors.extend(_validate_edge(edge))
    return errors

def assert_valid(data: dict) -> None:
    errors = validate_extraction(data)
    if errors:
        raise ValueError("\n".join(errors))

def _validate_node(node: dict) -> list[str]:
    # Checks: required fields present, type in VALID_FILE_TYPES

def _validate_edge(edge: dict) -> list[str]:
    # Checks: required fields present, confidence label in VALID_CONFIDENCES
```

### How validate.py is used

```python
# graph/build.py
from codeknow.validate import validate_extraction

def build_from_json(data):
    errors = validate_extraction(data)
    if errors:
        logging.warning("Validation errors: %s", errors)
    # ... proceed with building even if there are errors
```

Validation is **advisory** — it logs warnings but doesn't block graph building. This is important: LLM-generated extraction data may have minor issues that shouldn't prevent graph construction.

## Problem: Drift risk

If a new `Node.type` value is added (e.g., `"interface"`), it must be added to:
1. `VALID_FILE_TYPES` in validate.py — otherwise validation warns about valid data
2. Any code that switches on node type (e.g., `graph/analyze.py`)

The Pydantic model doesn't enforce the type value, so there's no single source of truth.

Similarly, if `ConfidenceLabel` gains a new value, `VALID_CONFIDENCES` in validate.py must be updated separately.

## Proposed solution

Move all validation into Pydantic field validators inside schemas.py.

### Approach

```python
# schemas.py — enriched with validators

class Node(BaseModel):
    id: str
    label: str
    type: str  # validated by field validator below
    file: str = ""
    source_location: str = ""
    extra: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        # Advisory: log warning but don't reject
        valid = {"file", "class", "function", "method", "module", "variable"}
        if v not in valid:
            import logging
            logging.getLogger(__name__).warning("Unknown node type: %s", v)
        return v


class Edge(BaseModel):
    source: str
    target: str
    relation: str
    confidence: float = 1.0
    confidence_label: str = "extracted"
    extra: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="allow")

    @field_validator("confidence_label")
    @classmethod
    def _validate_confidence(cls, v: str) -> str:
        valid = {e.value for e in ConfidenceLabel}
        if v not in valid:
            import logging
            logging.getLogger(__name__).warning("Unknown confidence label: %s", v)
        return v
```

### Advisory validation function (replaces validate.py)

The current usage in `build.py` is advisory (logs warnings, doesn't block). This can be preserved:

```python
# schemas.py

def validate_extraction(data: dict) -> list[str]:
    """Advisory validation — returns warnings, doesn't raise."""
    warnings: list[str] = []
    try:
        result = ExtractionResult.model_validate(data)
    except ValidationError as e:
        # Convert Pydantic errors to simple strings
        for err in e.errors():
            warnings.append(f"{err['loc']}: {err['msg']}")
    return warnings
```

### What gets deleted

- `validate.py` entirely (140 lines)
- `VALID_FILE_TYPES`, `VALID_CONFIDENCES`, `REQUIRED_NODE_FIELDS`, `REQUIRED_EDGE_FIELDS` constants
- `_validate_node()`, `_validate_edge()` functions
- `assert_valid()` function (replaced by `ExtractionResult.model_validate()` which raises on error)

## Migration path

1. Add field validators to `Node` and `Edge` in schemas.py
2. Add `validate_extraction()` to schemas.py that wraps Pydantic validation
3. Update `graph/build.py` to import `validate_extraction` from schemas instead of validate
4. Verify all existing tests pass
5. Delete validate.py

### Impact on existing callers

| Caller | Current | After |
|---|---|---|
| `graph/build.py` | `from codeknow.validate import validate_extraction` | `from codeknow.schemas import validate_extraction` |
| `e2e/graph_gen/test_graph_gen.py` | Uses `ExtractionResult` indirectly via pipeline | No change |
| `tests/test_build.py` | Uses `build_from_json` which calls `validate_extraction` | No change (build.py import path changes, test calls same functions) |

## Wins

- **locality**: one source of truth for valid values, not two
- **delete 140 lines** of hand-rolled validation
- **leverage**: Pydantic validators work at construction, JSON parse, and test time
- **drift eliminated**: adding a new type or confidence label means updating one place

## Risks / considerations

- **Advisory vs strict validation**: The current behavior logs warnings but continues. Pydantic validators can either log warnings (advisory) or raise errors (strict). The advisory behavior must be preserved — LLM-generated data may have minor issues that shouldn't block the pipeline.
- **Performance**: Pydantic validation on every Node/Edge construction adds overhead. The current dict-based validation only runs once during graph building. If this is a concern, the validators can be made optional (controlled by a model config flag).
- **`confidence` field vs `confidence_label`**: The current `Edge` model has a `confidence: float` field. The validation in validate.py checks `confidence_label` (a string). These are different concepts — `confidence` is a numeric score, `confidence_label` is the categorical label. The schema should clarify whether both are needed or if they can be unified.
- **Graph built from dicts, not Pydantic models**: `build.py` works with raw dicts from `ExtractionResult` JSON, not Pydantic model instances. The validation in build.py validates the raw dicts. If validation moves to Pydantic, build.py needs to parse dicts through Pydantic first (which it may already do indirectly).
