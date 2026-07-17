# Plan: Reindex on Git update

## Goal

When a repository changes, reuse work for unchanged files. Recompute only changed file chunks and embeddings. Rebuild the graph from cached file extractions so cross file relationships stay correct.

The build must also meet these rules:

1. A failed build must not replace the active index.
2. Search must read one complete graph and vector generation.
3. File paths and node IDs must stay stable between builds.
4. A forced push or missing old commit must fall back to a full build.

## Main design decisions

### Rebuild the graph from cached extractions

Run full file discovery and graph assembly on every changed commit. Pass every code file to the extractor with the repository root fixed explicitly. The file extraction cache means unchanged files do not need another parse.

Do not use `build_merge` for this feature. It cannot remove entities deleted from modified files, and partial extraction cannot rebuild cross file import and call edges correctly.

### Limit chunking and embedding to changed files

Load the prior chunk map and reuse entries for unchanged files. Rebuild entries for added and modified files. Remove entries for deleted files.

Copy unchanged vectors into a staging Chroma collection. Generate embeddings only for vector records that are new in the staging collection.

### Publish a complete generation

Each successful build gets a generation ID. A generation owns these files and one Chroma collection:

```text
<graph_dir>/<slug>/
  current.json
  generations/
    <generation_id>/
      graph.json
      chunk_map.json
      metadata.json

Chroma collection: codeknow_<slug>_<generation_id>
```

Write and verify the new generation before replacing `current.json`. Search reads `current.json` once and then uses only the named generation. Keep the previous generation until no current request can use it.

### Store repository relative paths

Persist POSIX paths relative to the repository root in graph nodes, chunks, vector metadata, and chunk map keys. Convert Git paths through one path helper. Reject paths that escape the repository root.

Chunking and extraction still read absolute paths. Their APIs must accept the repository root separately from the stored relative path.

### Give each vector record a file identity

Content hash alone is not a safe Chroma ID because two files can contain the same text. Add a stable vector ID based on the repository relative path, line range, and content hash.

```text
vector_id = sha256(relative_path + "\0" + start_line + "\0" + end_line + "\0" + content_hash)
```

Keep the content hash as metadata. Graph chunk references and vector lookups use `vector_id`.

## Required fixes before incremental builds

1. Set `PipelineResult.commit_hash` before calling `save_pipeline_result`. Existing builds currently write `null` into `metadata.json`.
2. Add an index schema version to metadata. Existing indexes without the new path and vector ID format require one full rebuild.
3. Change the extractor API so callers always provide the repository root. The extractor must not infer the root from the selected file list.
4. Change search and list operations to resolve the active generation through `current.json`.
5. Add an extraction cache version to cache keys. An extractor change must not reuse old extraction data.
6. Add a build fingerprint to metadata. Include the schema version, extraction version, embedding provider and model, chunk settings, and discovery settings.

## Git update flow

Do not use `git pull`. The managed clone should not create local merge commits.

1. Run `git fetch --prune origin`.
2. Resolve the remote commit from the tracked upstream branch. Record the branch name in metadata.
3. Read the active generation commit as `old_sha`.
4. If `old_sha == new_sha` and the build fingerprint matches, return the active generation without changing it.
5. Verify that both commits exist locally.
6. Check out `new_sha` in detached mode in the managed clone.
7. Read the diff with rename detection and NUL separated output.

```text
git diff --name-status -z --find-renames <old_sha> <new_sha>
```

Treat a rename as deletion of the old path and addition of the new path. Treat a copy as an addition. Support added, copied, deleted, modified, renamed, and type changed statuses.

Use a full build when the old commit is missing, the remote branch changed, the build fingerprint changed, or the active generation is incomplete.

## Incremental build algorithm

### 1. Prepare

1. Acquire the build lock for the slug.
2. Read `current.json` and validate every active generation file.
3. Fetch the remote commit and check out `new_sha`.
4. Run full file discovery.
5. Normalize all discovered and changed paths to repository relative POSIX paths.
6. Create a staging generation directory and staging Chroma collection.

### 2. Determine file changes

Start with the Git diff, then compare the old chunk map file set with the new discovery file set. The set comparison handles changes to `.graphignore` and file classification.

```text
added = discovered_paths - old_paths
deleted = old_paths - discovered_paths
modified = git_modified paths that remain discovered
changed = added union deleted union modified
unchanged = discovered_paths - added - modified
```

If `.graphignore` or another discovery rule changed, the full discovery result remains authoritative.

### 3. Build the graph

1. Pass every discovered code file and the fixed repository root to the extractor.
2. Reuse cached extraction results by content and repository relative path.
3. Rebuild the whole graph from all extraction results.
4. Rebuild all cross file relationships.
5. Run community detection on the new graph.

Graph assembly and community detection process the whole graph. Parsing remains incremental.

### 4. Build the chunk map

1. Copy old chunk entries for unchanged files.
2. Chunk added and modified files.
3. Drop deleted files.
4. Rebuild graph chunk references from the complete new chunk map.
5. Verify that every graph chunk reference exists in the new chunk map.

### 5. Build the vector collection

1. Copy vector records for unchanged files from the active collection to the staging collection, including their embeddings and documents.
2. Generate embeddings for added and modified file chunks.
3. Build graph metadata for every vector record after community detection.
4. Update copied vector metadata without generating embeddings again.
5. Verify record count, vector IDs, file paths, and required metadata.

Copying existing embeddings avoids provider calls for unchanged content. A Chroma `upsert` does not avoid an embedding call by itself.

### 6. Publish

1. Write graph, chunk map, and metadata into the staging generation directory.
2. Include the commit, branch, build fingerprint, collection name, counts, and build time in metadata.
3. Read every staging file back and validate it.
4. Run a small vector lookup against the staging collection.
5. Atomically replace `current.json` with the staging generation ID and collection name.
6. Release the build lock.
7. Remove old generations after a grace period.

The pointer replacement is the only publish step. A crash before the replacement leaves the prior generation active.

## First build and explicit rebuild

A first build uses the same generation layout but has no source generation to copy. An explicit rebuild skips all extraction and vector reuse.

Indexes without `current.json`, a commit hash, or the current schema version receive one full rebuild and migration into the generation layout.

## Concurrency and recovery

Allow one build per slug. Search may continue against the active generation during a build.

On startup and before each build, remove abandoned staging directories and staging collections that are not named by `current.json`. Never delete the active or previous generation during recovery.

Delay old generation cleanup so a search that already read the old pointer can finish. Keep the latest two generations. Remove older generations only after a configured grace period.

## Files to change

| File | Change |
|---|---|
| `git_download/downloader.py` | Add fetch, remote commit resolution, detached checkout, and safe diff parsing. |
| `pipeline/config.py` | Add update mode, schema version, and generation settings. |
| `pipeline/runner.py` | Split full and incremental preparation while sharing graph assembly and publish steps. Set commit before saving. |
| `pipeline/facade.py` | Keep managed clones, add the slug build lock, and stop unconditional cleanup. |
| `extract/extractor.py` | Require a fixed repository root for cache keys and node IDs. |
| `cache/hash.py` | Include the extraction cache version in cache keys. |
| `chunking/chunker.py` | Separate the absolute read path from the stored repository relative path. |
| `schemas.py` | Add `vector_id` to chunks and chunk references. |
| `pipeline/chunk_stage.py` | Reuse unchanged chunk entries and rebuild all graph references. |
| `pipeline/embed_stage.py` | Copy unchanged vectors, embed changed vectors, and refresh metadata. |
| `vector/embeddings.py` and `vector/ingest.py` | Read relative chunk paths through the repository root and use vector IDs. |
| `vector/chroma.py` | Add collection copy, metadata update, collection validation, and staging cleanup methods. |
| `pipeline/io.py` | Add generation read, validation, and atomic pointer replacement. |
| `vector/search.py` | Resolve graph files and the Chroma collection from the active generation. |
| `codeknow_api/app.py` | Request update mode by default and retain explicit full rebuild support. |

`build_merge`, the mtime manifest, and `delete_by_file` are not part of the new update path.

## Tests

### Git tests

1. Added, copied, deleted, modified, renamed, and type changed paths parse correctly.
2. Paths containing tabs and newlines parse correctly.
3. A forced push with an available old commit produces a valid diff.
4. A missing old commit causes a full build.
5. Fetch and checkout do not create a merge commit.

### Identity tests

1. Paths are repository relative and use POSIX separators.
2. A path outside the repository is rejected.
3. Extraction produces the same node IDs for one changed file and a full file list.
4. Identical content in two files produces different vector IDs.

### Incremental tests

1. An unchanged file uses its extraction cache and copied embedding.
2. A modified file with a deleted function has no stale graph node.
3. A changed import updates edges to unchanged files.
4. A `.graphignore` change adds and removes the correct files.
5. A rename removes the old path and adds the new path.
6. Community metadata matches the new graph for copied and new vectors.
7. A second build at the same commit performs no writes.

### Publish and recovery tests

1. Failure during extraction leaves the active generation unchanged.
2. Failure during embedding leaves the active generation unchanged.
3. Failure while writing generation files leaves the active generation unchanged.
4. Failure during pointer replacement leaves either the old or new complete generation active.
5. Search during a build reads only the old generation.
6. Startup cleanup removes abandoned staging data but keeps active data.

### Migration tests

1. Metadata with a null commit hash causes a full build.
2. An index without `current.json` causes a full build.
3. An old schema version causes a full build.
4. A changed embedding model causes a full build.
5. A changed extraction version causes a full build and does not reuse old cache entries.

## Delivery order

1. Fix commit persistence and add schema versioning and build fingerprints.
2. Add canonical paths and stable vector IDs.
3. Add generation storage and update search resolution.
4. Add deterministic Git fetch and diff support.
5. Add cached graph rebuild and chunk map reuse.
6. Add staging collection copy and metadata refresh.
7. Add recovery, cleanup, integration tests, and migration tests.

Each step should keep the full rebuild path working.

## Estimate

The change is larger than the earlier estimate because correct publication and vector identity require storage changes.

| Work | Estimate |
|---|---|
| Commit fix, schema version, and migration gate | 0.5 to 1 day |
| Canonical paths and stable IDs | 1 to 2 days |
| Generation storage and search changes | 1.5 to 2.5 days |
| Git fetch and diff handling | 0.5 to 1 day |
| Cached graph rebuild and chunk reuse | 1 to 2 days |
| Vector copy, metadata refresh, and cleanup | 1.5 to 2.5 days |
| Tests and failure injection | 2 to 3 days |

Expected total is 8 to 14 engineering days.

## Success criteria

1. A one file edit causes one file's chunks to be generated and embedded.
2. Unchanged files keep correct graph edges and current community metadata.
3. Identical text in different files remains searchable at both paths.
4. Search never observes a mixed graph and vector generation.
5. A failed update leaves the prior index usable.
6. A no change update performs no writes and no embedding calls.
