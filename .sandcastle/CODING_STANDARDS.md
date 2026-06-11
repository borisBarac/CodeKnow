# Coding Standards

## Style

- **Python 3.10+** — use `X | Y` union syntax (not `Optional[X]`), `list[X]` (not `List[X]`), etc.
- **First import** in every file: `from __future__ import annotations`
- **Import order** (enforced by ruff/isort): stdlib → third-party → local. Use `if TYPE_CHECKING:` guard for heavy or circular-import-prone types.
- **Naming**: `snake_case` for functions/variables/modules, `PascalCase` for classes/types/protocols, `UPPER_SNAKE_CASE` for constants. Private members prefixed with `_`.
- **Type annotations**: required on all production code (enforced by mypy `disallow_untyped_defs`). Use `Any` sparingly but it is allowed.
- **Docstrings**: Google-influenced style. Not required on every function/class but encouraged for public APIs. Use double backticks for inline code references.
- **Section headers**: use `# -- Section Name ---` dividers to organize longer files.
- **Keyword-only arguments**: use `*` separator for optional/configuration parameters in public APIs.
- **Error messages**: use local `msg` variable before raising: `msg = "..."; raise ValueError(msg)`. Chain exceptions with `from exc`.
- **Logging**: `logger = logging.getLogger(__name__)`; use `%s` formatting (not f-strings) in log calls.

## Data Models

- **Pydantic v2 `BaseModel`** for serialized/API schemas with `Field(...)` validators.
- **`@dataclass(frozen=True)`** for internal immutable containers; update via `dataclasses.replace()`.
- **`typing.Protocol`** with `@runtime_checkable` for abstract interfaces.
- **Factory functions** for backend selection (e.g., `get_cache_store()` returning a `CacheStore` protocol).
- **`__all__`** in every `__init__.py` to define explicit public exports.

## Testing

- **Test files**: `test_<module>.py` in `packages/codeknow-lib/tests/`, one per source module.
- **No type annotations required** in test code (relaxed ruff/mypy rules).
- **Flat test functions** preferred; class-based grouping (`Test*`) only when related tests share setup.
- **Helper factories** prefixed `_make_` (e.g., `_make_config()`, `_make_result()`) for constructing test data.
- **Fixtures** via `@pytest.fixture`; JSON fixture files in `tests/fixtures/`.
- **Mocking**: `unittest.mock.patch` / `MagicMock`.
- **`assert` statements** and private member access are fine in tests.

## Architecture

- **Source layout**: `src/codeknow/` with domain sub-packages (`cache/`, `chunking/`, `extract/`, `git_download/`, `graph/`, `pipeline/`, `vector/`).
- Each sub-package `__init__.py` acts as a **facade** re-exporting public API.
- **Pipeline architecture**: linear sequence of named stages with well-defined I/O contracts.
- **Dependency injection** via keyword-only function parameters defaulting to `None`, resolved to real implementations at call time.
- **Lazy imports** for optional/heavy dependencies (tree-sitter, langchain) inside function bodies.
- **Graceful degradation**: catch `Exception` and return empty/default results rather than crashing on parse or I/O failures.
- **Compiled regex** as module-level constants (e.g., `_CONTROL_CHAR_RE = re.compile(...)`).
- **`frozenset`** for immutable configuration sets.

## Linting & Type Checking

- **ruff**: `select = ["ALL"]` with curated ignores (complexity limits relaxed, some docstring rules off, `BLE001`/`ANN401` allowed).
- **mypy**: strict (`disallow_untyped_defs = true`) on production code only; tests/e2e excluded.
- **Formatter**: ruff format (no trailing commas enforced).
