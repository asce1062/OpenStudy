# Interview Engineering Curriculum

Interview Engineering is an adaptive mastery system built on top of OpenStudy.
It turns local curriculum source repositories into a seeded dashboard with
mastery units, lesson/practice atoms, retry metadata, source references, and
flashcard metadata. The daily agenda is the study plan: it chooses work from
learner evidence instead of walking a fixed syllabus calendar.

The system is designed for repeatable self-hosting. A maintainer can regenerate
the manifest, validate it, preview the database changes, and seed the dashboard
after deployment without hand-editing database rows.

## Documentation Map

- [Architecture](./architecture.md): system model, data flow, and tradeoffs.
- [Curriculum Manifest](./curriculum-manifest.md): schema v2, stable IDs,
  tracks, modules, lessons, dependencies, and inferred tasks.
- [Asset System](./asset-system.md): `.apkg` and `.db` discovery, metadata, and
  Anki workflow.
- [Extending the Curriculum](./extending-the-curriculum.md): adding sources,
  modules, lessons, assets, and schema checks.
- [Seeding OpenStudy](./seeding-openstudy.md): deterministic DB import,
  idempotency, dry runs, and metadata persistence.
- [Deployment](./deployment.md): platform-neutral deployment and curriculum
  seed lifecycle.
- [Coolify](./coolify.md): canonical current-state environment, migrations,
  operator email login, password replacement, storage, networking, and seed
  operations.
- [Domain and SSL](./domain-and-ssl.md): `learn.alexmbugua.me` example, DNS,
  reverse proxy, TLS, and health checks.
- [Developer Workflow](./developer-workflow.md): local commands, tests, linting,
  source updates, and common maintenance tasks.

## Current Shape

The canonical generated manifest is:

```text
curriculum/interview_manifest.v2.yaml
```

It is generated from:

```text
curriculum/manifests/interview-engineering.yaml
curriculum/sources/*
```

At the time of writing, the generated manifest contains:

- 7 source repositories
- 1 track
- 16 mastery units
- 113 lessons
- 6 flashcard assets
- elastic effort bands instead of fixed pacing targets

The course should answer what mastery unit is active, which phase it is in,
what retry or retention work is overdue, what evidence supports advancing, and
what should be delayed. It should not answer "what week are we in?"

## Core Commands

Generate the deterministic manifest:

```bash
python scripts/curriculum/create_manifest.py --force --verbose
```

Validate it:

```bash
python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
```

Preview seed changes:

```bash
python scripts/curriculum/seed_openstudy.py --dry-run --verbose
```

Seed OpenStudy:

```bash
python scripts/curriculum/seed_openstudy.py --verbose
```

Run the server-side helper after deployment and migrations:

```bash
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose
```

Inside Docker Compose, use the app container so the database environment is
already available:

```bash
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose
```

## Why A Manifest

The source repositories are large, differently structured, and maintained
upstream. OpenStudy should not copy or rewrite their content. The manifest
preserves attribution through repository IDs, relative paths, and optional
headings while adding OpenStudy-specific metadata such as elastic effort bands,
mastery state, retry defaults, difficulty, cognitive load, and completion
criteria.

This gives future automation a stable contract:

- import the same curriculum repeatedly without duplicates
- update existing rows by stable IDs
- preserve source attribution
- drive agenda-informed recommendation logic from learner state
- keep custom curriculum work isolated from upstream source repositories

See [Architecture](./architecture.md) for the end-to-end model.
