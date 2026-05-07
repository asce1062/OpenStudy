# Interview Engineering Curriculum

Interview Engineering is a manifest-driven software engineering interview
preparation system built on top of OpenStudy. It turns local curriculum source
repositories into a seeded OpenStudy dashboard with courses, study topics,
tasks, source references, flashcard metadata, and dependency information.

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
- [Deployment](./deployment.md): Docker deployment and post-deploy seed flow.
- [Coolify](./coolify.md): Git-based deployment, env vars, volumes, logs, and
  manual seeding.
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
- 16 modules
- 113 lessons
- 6 flashcard assets
- 281.8 estimated study hours

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
scripts/curriculum/deploy_seed_openstudy.sh --dry-run --verbose
scripts/curriculum/deploy_seed_openstudy.sh --verbose
```

Inside Docker Compose, use the app container so the database environment is
already available:

```bash
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --dry-run --verbose
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --verbose
```

## Why A Manifest

The source repositories are large, differently structured, and maintained
upstream. OpenStudy should not copy or rewrite their content. The manifest
preserves attribution through repository IDs, relative paths, and optional
headings while adding OpenStudy-specific metadata such as estimated effort,
difficulty, dependencies, cognitive load, and completion criteria.

This gives future automation a stable contract:

- import the same curriculum repeatedly without duplicates
- update existing rows by stable IDs
- preserve source attribution
- add scheduling and recommendation logic later
- keep custom curriculum work isolated from upstream source repositories

See [Architecture](./architecture.md) for the end-to-end model.

