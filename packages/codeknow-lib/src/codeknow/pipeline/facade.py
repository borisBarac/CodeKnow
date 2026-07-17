from __future__ import annotations

import json
import logging
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeknow.schemas import HybridSearchResponse, ListReposResponse, RepoMetadata

if TYPE_CHECKING:
    from collections.abc import Iterator

    from codeknow.vector.chroma import ChromaStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildResult:
    slug: str
    commit_hash: str | None = None
    node_count: int | None = None
    edge_count: int | None = None
    community_count: int | None = None


@dataclass(frozen=True)
class DeleteResult:
    slug: str
    chunks_deleted: int = 0


class PipelineFacade:
    """High-level interface that insulates callers from lib internals.

    Owns config, ChromaDB wiring, filesystem paths, and repo_map.
    Callers (API handlers, CLI) use this instead of PipelineConfig,
    ChromaConfig, EmbeddingConfig directly.
    """

    def __init__(
        self,
        *,
        graph_dir: Path | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        self._home = Path.home() / ".codeknow"
        self.graph_dir: Path = graph_dir or Path(
            os.getenv("CODEKNOW_GRAPH_DIR", str(self._home / "graph"))
        )
        self.temp_dir: Path = temp_dir or Path(
            os.getenv("CODEKNOW_TEMP_DIR", str(self._home / "temp"))
        )

    @staticmethod
    def resolve_slug(url: str) -> str:
        from codeknow.pipeline.config import PipelineConfig

        return PipelineConfig(repo_url=url).slug

    def slug_dir(self, slug: str) -> Path:
        return self.graph_dir / slug

    def has_slug(self, slug: str) -> bool:
        directory = self.slug_dir(slug)
        return (directory / "current.json").exists() or (
            directory / "metadata.json"
        ).exists()

    def _make_store(self, slug: str, collection_name: str | None = None) -> ChromaStore:
        from codeknow.vector.chroma import ChromaConfig, ChromaStore
        from codeknow.vector.embeddings import EmbeddingConfig, create_embeddings

        embeddings = create_embeddings(EmbeddingConfig())
        return ChromaStore(
            config=ChromaConfig(collection_name=collection_name or f"codeknow_{slug}"),
            embeddings=embeddings,
        )

    def resolve_url_for_slug(self, slug: str) -> str | None:
        from codeknow.git_download import get_url

        input_dir = Path(os.getenv("CODEKNOW_INPUT_DIR", str(self._home / "repos")))
        return get_url(input_dir / slug)

    def get_repo_path(self, url: str) -> Path | None:
        """Return the local clone path for *url*, or ``None`` if not downloaded."""
        from codeknow.git_download import get_path

        return get_path(url)

    def build(
        self,
        ssh_url: str,
        *,
        clean_first: bool = False,
        progress_callback: Any = None,
    ) -> BuildResult:
        from codeknow.pipeline import PipelineConfig, run_pipeline

        slug = PipelineConfig(repo_url=ssh_url).slug

        config = PipelineConfig(
            repo_url=ssh_url,
            input_dir=self.temp_dir,
            output_dir=self.graph_dir / slug,
            force_rebuild=clean_first,
        )

        kwargs: dict[str, Any] = {}
        if progress_callback is not None:
            kwargs["progress_callback"] = progress_callback

        with self._build_lock(slug):
            result = run_pipeline(config, **kwargs)

        return BuildResult(
            slug=slug,
            commit_hash=result.commit_hash,
            node_count=result.stats.get("nodes"),
            edge_count=result.stats.get("edges"),
            community_count=result.stats.get("communities"),
        )

    @contextmanager
    def _build_lock(self, slug: str) -> Iterator[None]:
        """Allow one build process per slug."""
        import fcntl

        lock_dir = self.graph_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        path = lock_dir / f"{slug}.lock"
        with path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def delete(self, slug: str) -> DeleteResult:
        if slug.startswith("."):
            msg = f"Refusing to delete internal directory: {slug}"
            raise ValueError(msg)
        with self._build_lock(slug):
            return self._delete_unlocked(slug)

    def _delete_unlocked(self, slug: str) -> DeleteResult:
        collection_names: set[str] = {f"codeknow_{slug}"}
        generation_ids: set[str] = set()
        pointer_path = self.slug_dir(slug) / "current.json"
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            for key in ("collection_name", "previous_collection_name"):
                if name := pointer.get(key):
                    collection_names.add(name)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        generations = self.slug_dir(slug) / "generations"
        if generations.is_dir():
            from codeknow.pipeline import load_metadata

            for generation in generations.iterdir():
                generation_ids.add(generation.name)
                try:
                    metadata = load_metadata(generation)
                except Exception:
                    logger.warning(
                        "Could not read generation metadata at %s",
                        generation,
                        exc_info=True,
                    )
                    continue
                if metadata and metadata.get("collection_name"):
                    collection_names.add(metadata["collection_name"])

        from codeknow.vector.chroma import ChromaConfig, list_collection_names

        listed_names = list_collection_names(ChromaConfig())
        if listed_names is None:
            msg = "Could not enumerate ChromaDB collections"
            raise RuntimeError(msg)
        for name in listed_names:
            if any(
                name.endswith(f"_{generation_id}") for generation_id in generation_ids
            ):
                collection_names.add(name)
        names_to_scan = listed_names

        chunks_deleted = 0
        failures: list[str] = []
        dropped_names: set[str] = set()
        for collection_name in names_to_scan:
            try:
                store = self._make_store(slug, collection_name)
                deleted = store.delete_by_slug(slug)
                chunks_deleted += deleted
                if collection_name in collection_names or (
                    deleted and store.count() == 0
                ):
                    store.drop_collection(strict=True)
                    dropped_names.add(collection_name)
            except Exception:  # noqa: PERF203 - isolate each collection failure
                failures.append(collection_name)
                logger.warning(
                    "ChromaDB deletion failed for slug '%s' collection '%s'",
                    slug,
                    collection_name,
                    exc_info=True,
                )
        remaining_names = list_collection_names(ChromaConfig())
        if remaining_names is None:
            failures.append("<collection verification>")
        else:
            failures.extend(sorted(dropped_names & remaining_names))
        if failures:
            msg = f"Failed to delete {len(failures)} ChromaDB collection(s)"
            raise RuntimeError(msg)

        shutil.rmtree(self.slug_dir(slug), ignore_errors=True)
        shutil.rmtree(self.temp_dir / slug, ignore_errors=True)

        url = self.resolve_url_for_slug(slug)
        if url:
            from codeknow.git_download import unregister

            unregister(url)

        return DeleteResult(slug=slug, chunks_deleted=chunks_deleted)

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        slugs: list[str] | None = None,
    ) -> HybridSearchResponse:
        from codeknow.vector.search import GraphSearcher

        return GraphSearcher.multi_search(
            self.graph_dir, query, top_k=top_k, slugs=slugs
        )

    def cleanup(self) -> list[DeleteResult]:
        """Delete all slugs: graph dirs, temp dirs, ChromaDB collections, repo_map."""
        results: list[DeleteResult] = []
        if self.graph_dir.is_dir():
            for child in sorted(self.graph_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    results.append(self.delete(child.name))
        return results

    def recover(self) -> None:
        """Clean abandoned generations and collections for every known slug."""
        if not self.graph_dir.is_dir():
            return
        from codeknow.pipeline import PipelineConfig, load_metadata
        from codeknow.pipeline.runner import _cleanup_old_generations

        for directory in sorted(self.graph_dir.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                metadata = load_metadata(directory) or {}
            except Exception:
                metadata = {}
            if not metadata.get("collection_name"):
                generations = directory / "generations"
                if generations.is_dir():
                    for candidate in sorted(generations.iterdir(), reverse=True):
                        try:
                            candidate_metadata = json.loads(
                                (candidate / "metadata.json").read_text(
                                    encoding="utf-8"
                                )
                            )
                        except (FileNotFoundError, json.JSONDecodeError):
                            continue
                        if candidate_metadata.get("collection_name"):
                            metadata = candidate_metadata
                            break
            generation_id = metadata.get("generation_id")
            collection_name = metadata.get("collection_name")
            collection_base = None
            if generation_id and collection_name:
                suffix = f"_{generation_id}"
                if collection_name.endswith(suffix):
                    collection_base = collection_name.removesuffix(suffix)
            config = PipelineConfig(
                repo_url=metadata.get("github_ssh_url", directory.name),
                output_dir=directory,
                chroma_collection=collection_base,
            )
            with self._build_lock(directory.name):
                _cleanup_old_generations(config)

    def list_repos(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        health_check: bool = False,
        build_status: dict[str, dict[str, Any]] | None = None,
    ) -> ListReposResponse:
        from codeknow.pipeline import load_metadata

        repos: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        if self.graph_dir.is_dir():
            for child in sorted(self.graph_dir.iterdir()):
                if not child.is_dir():
                    continue
                slug = child.name
                try:
                    meta = load_metadata(child)
                    if meta is None:
                        continue
                except Exception as exc:
                    errors.append({"slug": slug, "error": str(exc)})
                    continue

                if build_status and slug in build_status:
                    info = build_status[slug]
                    meta["build_status"] = info.get("status")
                    meta["build_progress"] = info.get("progress")

                if health_check:
                    try:
                        from codeknow.pipeline.io import load_current, load_graph

                        current = load_current(child)
                        graph_dir = current.directory if current else child
                        graph_filename = (
                            current.graph_filename if current else "graph.json"
                        )
                        load_graph(graph_dir / graph_filename)
                        meta["health"] = "ok"
                    except FileNotFoundError:
                        meta["health"] = "missing_graph"
                    except Exception as exc:
                        meta["health"] = f"error: {exc}"

                repos.append(meta)

        start = (page - 1) * page_size
        end = start + page_size
        paged = repos[start:end]

        return ListReposResponse(
            repos=[RepoMetadata(**r) for r in paged],
            total=len(repos),
            page=page,
            page_size=page_size,
            errors=errors,
        )
