# Curriculum Manifest

The curriculum manifest is the source of truth for importing Interview
Engineering into OpenStudy. It is YAML, schema version 2, and optimized for
future automation.

## Files

```text
curriculum/manifests/interview-engineering.yaml
curriculum/interview_manifest.v2.yaml
```

The curated file is maintained by humans. The generated file is produced by
`scripts/curriculum/create_manifest.py` and is the canonical input for
validation and seeding.

## Curated Base vs Generated Manifest

The curated base contains the richer learning path:

- 13 core modules
- 107 curated lessons
- source headings
- completion criteria
- dependencies
- difficulty and cognitive load
- inferred checkpoints and projects

The generator preserves that structure and enriches it with:

- all discovered source repositories
- `kind` and `course_code` source metadata
- discovered flashcard assets
- compact supplemental modules for `practice-c`, `practice-cpp`, and
  `practice-python`
- recalculated module and total estimated hours

The generated manifest currently has 16 modules and 113 lessons.

## Top-Level Shape

```yaml
meta:
  id: ie-curriculum-interview-engineering
  name: Interview Engineering
  schema_version: 2
  timezone: Africa/Nairobi
  estimated_total_hours: 281.8

sources:
  - id: coding-interview-university
    name: Coding Interview University
    path: curriculum/sources/coding-interview-university
    upstream: https://github.com/jwasham/coding-interview-university
    kind: core-curriculum
    course_code: CIU

tracks:
  - id: ie-track-core
    name: Interview Engineering Core
    suggested_order: 1
    modules: []

assets: []
```

## Schema Concepts

### Meta

`meta` describes the manifest as a whole. Important fields include:

- `id`: stable manifest ID
- `name`: display name
- `schema_version`: must be `2`
- `timezone`: used for learner planning context
- `owner`: curriculum owner
- `estimated_total_hours`: recalculated from lesson estimates
- `id_policy`: explains stable ID prefixes

### Sources

Sources define local repositories and attribution metadata.

```yaml
- id: system-design-primer
  name: System Design Primer
  path: curriculum/sources/system-design-primer
  upstream: https://github.com/donnemartin/system-design-primer
  kind: system-design
  course_code: SDP
```

Lesson `source.repo` values should reference a source ID unless the lesson is
synthetic or inferred.

### Tracks

Tracks are top-level learning paths. The current manifest has one track:

```yaml
id: ie-track-core
name: Interview Engineering Core
```

The schema supports `depends_on`, but the current track has no dependencies.

### Modules

Modules group lessons by study phase.

```yaml
- id: ie-module-system-design-foundations
  name: System Design Foundations
  difficulty:
    level: intermediate
    score: 5
  estimated_hours: 16.8
  tags:
    - system-design
  prerequisites:
    - ie-module-coding-challenge-practice-loop
  depends_on:
    - ie-module-coding-challenge-practice-loop
  suggested_order: 9
  lessons: []
```

The current modules are ordered from foundations through coding practice,
system design, capstone review, and supplemental implementation practice.

### Lessons

Lessons become learner-facing tasks during seeding.

```yaml
- id: ciu-asymptotic-notation
  title: Learn asymptotic notation
  type:
    category: reading
    medium: markdown
    interaction: passive
  source:
    repo: coding-interview-university
    path: README.md
    heading: Algorithmic complexity / Big-O / Asymptotic analysis
  estimated_minutes: 150
  completion_criteria: Distinguish Big-O, Omega, and Theta on common loops and nested loops.
  suggested_order: 1
  depends_on:
    - ie-baseline-diagnostic
  difficulty:
    level: beginner
    score: 1
  cognitive_load: medium
```

Required lesson concepts:

- stable `id`
- structured `type`
- source reference
- positive estimated minutes
- completion criteria
- local ordering
- dependency list
- difficulty level and numeric score
- cognitive load

### Structured Types

Lesson type uses three dimensions:

```yaml
type:
  category: reading
  medium: markdown
  interaction: passive
```

Valid categories:

- `reading`
- `coding-exercise`
- `project`
- `checkpoint`

Valid media:

- `markdown`
- `notebook`
- `video`
- `reference`
- `manifest`

Valid interactions:

- `passive`
- `active`
- `constructive`
- `reflective`

### Inferred Tasks

Inferred lessons are synthetic OpenStudy tasks that organize learning but are
not directly copied from a source repository.

```yaml
- id: ie-baseline-diagnostic
  type:
    category: checkpoint
    medium: manifest
    interaction: reflective
  inferred: true
```

Use inferred tasks for checkpoints, projects, reviews, diagnostics, and mock
interview preparation.

### Dependencies

Dependencies use stable IDs:

```yaml
depends_on:
  - ciu-arrays-and-strings
```

The validator resolves references across track, module, and lesson IDs and
detects circular dependencies. The seed script stores dependency data in task
metadata for future scheduling and recommendation logic.

## Determinism

The generator is deterministic:

- source directories are sorted
- assets are sorted by ID
- generated YAML uses stable ordering
- no timestamps are written
- module hours are recalculated from lesson minutes

This makes diffs reviewable and lets deployment regenerate the same manifest.

## Validation

Run:

```bash
python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
```

The validator checks:

- YAML parsing
- required keys
- schema version
- source paths
- stable kebab-case IDs
- uniqueness
- lesson source references
- structured type values
- difficulty and cognitive load values
- dependency resolution and cycles
- heading existence warnings
- asset structure

Warnings do not block seeding. Errors do.

## Common Mistakes

- Reusing a lesson ID for a renamed lesson.
- Editing the generated manifest but forgetting to update the curated base or
  generator.
- Adding a lesson source path that does not exist under the declared source
  repository.
- Adding too many granular practice lessons and making the dashboard noisy.
- Changing `estimated_hours` manually instead of letting the generator
  recalculate it from lessons.

