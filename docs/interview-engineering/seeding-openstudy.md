# Seeding OpenStudy

`scripts/curriculum/seed_openstudy.py` imports a schema v2 curriculum manifest
into the local OpenStudy database. It is deterministic and idempotent.

## Commands

Dry run:

```bash
python scripts/curriculum/seed_openstudy.py --dry-run --verbose
```

Apply:

```bash
python scripts/curriculum/seed_openstudy.py --verbose
```

Use an explicit manifest:

```bash
python scripts/curriculum/seed_openstudy.py \
  --manifest curriculum/interview_manifest.v2.yaml \
  --dry-run \
  --verbose
```

The script expects the same database environment as the backend:

```text
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_DB
PGHOST
PGPORT
```

Inside the `openstudy` container, these come from Coolify/Compose-injected
environment variables.

For deployed Coolify containers, prefer the helper with the prebuilt manifest:

```bash
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose
```

## What Gets Seeded

| Manifest concept | OpenStudy persistence |
| --- | --- |
| Manifest meta | `courses` row plus JSON metadata in `courses.notes` |
| Track | `events` row with `kind = curriculum:track` |
| Source | `events` row with `kind = curriculum:source` |
| Asset | `events` row with `kind = curriculum:asset` |
| Module / mastery unit | `study_topics` row |
| Lesson / practice atom | `tasks` row |
| Dependencies | JSON metadata on tasks/modules/course events |

The current OpenStudy schema keeps UI compatibility by using existing tables.
Adaptive state is stored in first-class mastery/retry columns plus metadata
fields for attribution and manifest details.

## Mapping Details

### Course

The seed script uses OpenStudy course code `IE` by default. The manifest ID is
stored in metadata and `module_code`.

Natural key:

```text
courses.code = IE
```

### Modules

Modules become mastery-unit rows in `study_topics`.

Important fields:

- `course_code`: `IE`
- `chapter`: track name
- `name`: module name
- `description`: module description
- `kind`: `reading`
- `sort_order`: weak tie-breaker from module `suggested_order`
- `mastery_state`: current adaptive phase, initially `not_started`
- retry columns: `retry_count`, `last_attempted_at`, `last_completed_at`,
  `last_reviewed_at`, `next_review_at`, `last_confidence`, `error_count`,
  `failure_reason`, `struggle_tags`, `retry_priority`
- `notes`: marker plus JSON metadata

### Lessons

Lessons become rows in `tasks`.

Important fields:

- `course_code`: `IE`
- `title`: lesson title
- `description`: marker plus JSON metadata
- `priority`: `high` for high cognitive load, otherwise `med`
- `tags`: curriculum, lesson ID, module ID, category, cognitive load

The metadata includes:

- manifest ID
- schema version
- track ID
- module ID
- lesson ID
- type
- difficulty
- cognitive load
- completion criteria
- elastic effort band / duration hint
- source reference
- inferred flag
- dependencies as attribution/context, not rigid gates
- suggested order as a weak tie-breaker
- mastery state and retry metadata

Lesson tasks are not arbitrary dated milestones. They are exposure, reading,
guided practice, independent practice, timed execution, retry, or retention
atoms that the agenda can choose when learner state supports it.

### Sources, Assets, And Tracks

These are represented as `events` rows because OpenStudy does not currently
have dedicated tables for them.

Example event kinds:

```text
curriculum:source
curriculum:asset
curriculum:track
```

Each event payload contains a `seed_id` for idempotent update behavior.

## Idempotency

The seed script uses stable manifest IDs as natural keys. Running the same seed
again updates existing rows.

First run against an empty database:

```text
Courses: create=1, update=0
Sources: create=7, update=0
Assets: create=6, update=0
Tracks: create=1, update=0
Modules: create=16, update=0
Lessons/tasks: create=113, update=0
```

Second run:

```text
Courses: create=0, update=1
Sources: create=0, update=7
Assets: create=0, update=6
Tracks: create=0, update=1
Modules: create=0, update=16
Lessons/tasks: create=0, update=113
```

The marker format prevents prefix collisions between IDs such as
`ciu-second-lesson` and `ciu-second-lesson-extra`.

## Dry Run

`--dry-run` validates the manifest, connects to the database, reads existing
seeded rows, and prints planned creates or updates. It does not mutate the
database.

Use it before every production seed:

```bash
python scripts/curriculum/seed_openstudy.py --dry-run --verbose
```

## Reset Mode

The script supports:

```bash
python scripts/curriculum/seed_openstudy.py --reset --verbose
```

This is destructive for previously seeded data for the selected course. Prefer
normal idempotent updates unless you intentionally want to clear and reseed.

## Seed Flow

```mermaid
flowchart TD
    Manifest["interview_manifest.v2.yaml"] --> Validate["internal manifest validation"]
    Validate --> Course["upsert courses"]
    Course --> Events["upsert source/asset/track events"]
    Events --> Modules["upsert study_topics"]
    Modules --> Lessons["upsert tasks"]
    Lessons --> Summary["print create/update/delete counts"]
```

## Troubleshooting

### `POSTGRES_USER` Missing

Run inside the OpenStudy container or export the DB env vars:

```bash
docker compose exec openstudy python scripts/curriculum/seed_openstudy.py --dry-run --verbose
```

### Manifest Validation Fails

Run the validator directly:

```bash
python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
```

Fix errors before seeding. Warnings do not block seeding.

### Assets Warn As Missing

In development, check that source submodules are present and that the asset path
exists under the expected source repository.

In production, check that the image contains the six whitelisted flashcard files
under `/app/curriculum/sources`. The image intentionally excludes full source
repositories.

### Duplicate Rows Appear

Do not manually edit seed markers in `courses.notes`, `study_topics.notes`, or
`tasks.description`. They are used to find existing rows.
