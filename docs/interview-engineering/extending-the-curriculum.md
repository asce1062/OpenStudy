# Extending The Curriculum

The safest way to extend Interview Engineering is to preserve the curated base
manifest, update generator enrichment rules only where needed, and validate the
generated manifest before seeding.

## Extension Principles

- Keep IDs stable forever once seeded.
- Prefer references to source files over copied content.
- Keep generated output deterministic.
- Keep source submodules untouched unless intentionally updating upstream refs.
- Add lessons at the right granularity for dashboard use.
- Add tests when generator or validator behavior changes.

## Add A New Source Repository

1. Add the repository under `curriculum/sources/`.
2. Add source metadata to `SOURCE_CONFIG` in
   `scripts/curriculum/create_manifest.py`.
3. Decide whether it becomes core modules, supplemental modules, assets, or
   source references only.
4. Regenerate and validate.

Example `SOURCE_CONFIG` entry:

```python
"example-source": SourceConfig(
    name="Example Source",
    upstream="https://github.com/example/source",
    course_code="EX",
    prefix="ex",
    kind="implementation-practice",
)
```

Then update validation if the new lesson ID prefix should be accepted.

## Add A Curated Module

For core learning path changes, edit:

```text
curriculum/manifests/interview-engineering.yaml
```

Add the module under the track with:

- stable `id`
- clear `name` and `description`
- `difficulty.level` and `difficulty.score`
- lowercase kebab-case `tags`
- `prerequisites`
- `depends_on`
- sequential `suggested_order`
- a non-empty `lessons` list

Regenerate:

```bash
python scripts/curriculum/create_manifest.py --force --verbose
```

Validate:

```bash
python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
```

## Add A Lesson

Use a globally unique ID with the correct prefix:

- `ciu-` for Coding Interview University references
- `icc-` for Interactive Coding Challenges references
- `sdp-` for System Design Primer references
- `pc-`, `pcpp-`, `py-` for practice repositories
- `ie-` for inferred OpenStudy checkpoints and projects

Example:

```yaml
- id: sdp-design-url-shortener-review
  title: Review URL shortener design tradeoffs
  type:
    category: checkpoint
    medium: manifest
    interaction: reflective
  inferred: true
  source:
    repo: system-design-primer
    path: solutions/system_design/pastebin/README.md
  estimated_minutes: 90
  completion_criteria: Explain the API, data model, caching strategy, and scaling bottlenecks.
  suggested_order: 4
  depends_on:
    - sdp-load-balancers
  difficulty:
    level: advanced
    score: 7
  cognitive_load: high
```

## Add Assets

Asset discovery is automatic for `.apkg` and `.db` files under source
repositories. For known special cases, add or adjust logic in
`asset_for_path()` in `create_manifest.py`.

Add tests if the expected ID, title, course code, or storage path matters.

## Expand A Practice Repository

The practice repositories are compact by design. Before expanding them:

1. Decide whether the dashboard benefits from more tasks.
2. Group by concept, not by every file.
3. Keep lessons high-level enough to be actionable.
4. Preserve the existing supplemental module IDs.

Good expansion:

- one lesson for array implementation drills
- one lesson for hash table implementation drills
- one checkpoint for comparing language tradeoffs

Poor expansion:

- one lesson per source file
- one lesson per function
- lessons with no completion criteria

## Update Validation

Update `scripts/curriculum/validate_manifest.py` when you add:

- new lesson ID prefixes
- new asset kinds or formats
- new structured type categories
- stricter schema rules
- new top-level sections

Validation should catch mistakes without blocking harmless future metadata. Use
warnings for non-critical hygiene and errors for broken imports.

## Preserve Determinism

When adding generation logic:

- sort directory walks
- sort generated assets and modules where practical
- avoid timestamps
- avoid environment-dependent values
- use stable IDs derived from known source IDs and filenames

The generator test checks deterministic output by building the same manifest
twice and comparing YAML dumps.

## Safe Schema Evolution

Schema changes should be additive until importers are ready for a migration.

Recommended order:

1. Add optional fields to generated manifests.
2. Teach the validator to allow and check them.
3. Teach the seeder to persist them in metadata.
4. Add tests.
5. Only then consider database migrations or UI changes.

## Common Mistakes

- Changing an existing ID because a title changed.
- Adding a source repo but forgetting `SOURCE_CONFIG`.
- Adding generated modules that reorder curated modules unexpectedly.
- Adding validation errors for harmless extra metadata.
- Seeding before validating.

