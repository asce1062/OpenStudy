# Developer Workflow

This page covers local development and maintenance for the Interview
Engineering curriculum pipeline.

## Generate The Manifest

```bash
python scripts/curriculum/create_manifest.py --force --verbose
```

Write somewhere else:

```bash
python scripts/curriculum/create_manifest.py \
  --output /tmp/interview_manifest.v2.yaml \
  --force \
  --verbose
```

Dry-run generation to stdout:

```bash
python scripts/curriculum/create_manifest.py --dry-run
```

## Validate

```bash
python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
```

Warnings are acceptable when intentional. Errors must be fixed before seeding.

## Seed Locally

If your shell has DB env vars:

```bash
python scripts/curriculum/seed_openstudy.py --dry-run --verbose
python scripts/curriculum/seed_openstudy.py --verbose
```

In Docker Compose:

```bash
docker compose exec openstudy python scripts/curriculum/seed_openstudy.py --dry-run --verbose
docker compose exec openstudy python scripts/curriculum/seed_openstudy.py --verbose
```

Server-side helper:

```bash
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --dry-run --verbose
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --verbose
```

In deployed Coolify containers, use the prebuilt manifest:

```bash
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
docker compose exec openstudy scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose
```

## Tests And Static Checks

Run curriculum tests:

```bash
python -m pytest tests/curriculum
```

Run project checks:

```bash
python -m ruff check .
python -m pyright
```

The curriculum seed tests use the repository's Docker-backed Postgres fixtures.
Docker must be available.

## Updating Source Repositories

Source repositories live under:

```text
curriculum/sources/
```

If they are Git submodules in your checkout, update them with normal submodule
commands:

```bash
git submodule update --init --recursive
git submodule update --remote curriculum/sources/system-design-primer
```

After updating source repos:

```bash
python scripts/curriculum/create_manifest.py --force --verbose
python scripts/curriculum/validate_manifest.py curriculum/interview_manifest.v2.yaml
python -m pytest tests/curriculum
```

Review the generated manifest diff before committing.

## Adding Generator Tests

Use `tests/curriculum/test_create_manifest.py` for deterministic generation and
asset discovery behavior. Good assertions include:

- generated YAML is stable between runs
- source IDs are sorted
- expected assets are discovered
- curated module and lesson baseline is preserved
- supplemental practice modules exist

Use `tests/curriculum/test_seed_openstudy.py` for database behavior:

- dry-run does not mutate tables
- seed is idempotent
- assets are represented
- dependencies are preserved
- missing asset files warn

## Review Checklist

Before opening a PR or merging:

- Generated manifest exists and validates.
- New IDs are stable and globally unique.
- Source paths exist.
- Dependencies resolve.
- Asset paths exist or intentionally warn.
- Tests pass.
- `ruff` and `pyright` pass.
- Deployment helper dry-run has been tested against a real database.

## Common Mistakes

- Running tests without Docker available.
- Editing only `curriculum/interview_manifest.v2.yaml`; generator will overwrite
  those edits.
- Forgetting to update tests when asset IDs or source configs change.
- Changing a seeded ID instead of adding a new lesson.
- Running seed scripts against the wrong database environment.

## Future Improvements

- Add a dedicated curriculum schema module shared by generator, validator, and
  seeder.
- Add first-class database tables for curriculum sources, assets, and
  dependencies.
- Add dashboard UI for asset review cadence.
- Add a generated documentation summary from the manifest.
- Add importer support for adaptive scheduling based on dependency graph and
  cognitive load.
