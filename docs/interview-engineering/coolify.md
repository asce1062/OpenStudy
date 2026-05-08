# Coolify Deployment

Coolify can deploy OpenStudy from Git using the repository Docker Compose file.
The curriculum seed remains a manual post-deploy operation.

This page avoids Coolify-specific assumptions beyond common Git and Docker
Compose behavior. Adjust names to match your project and server.

## Recommended Shape

- Source: Git repository containing OpenStudy.
- Build: Docker Compose.
- Services: `postgres`, `openstudy`, `frontend`.
- Persistent database volume or bind mount for Postgres data.
- Persistent course file bind mount for `/opt/courses`.
- Public domain points to the `frontend` service.
- Compose does not define custom networks; Coolify manages service/proxy
  networking.
- The `frontend` service exposes internal port `80` and carries the Traefik
  service-port label.

## Environment Variables

The Compose file is configured for Coolify-injected environment variables. Add
these values in Coolify's environment manager rather than relying on
repository-local `env_file` entries.

Required application values:

```text
APP_PASSWORD_HASH=...
SESSION_SECRET=...
```

Required database values:

```text
POSTGRES_USER=openstudy
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=openstudy
```

Recommended domain/build values:

```text
PUBLIC_SITE_URL=https://learn.alexmbugua.me
PUBLIC_SITE_NAME=OpenStudy
PUBLIC_SHOW_LANDING=false
PUBLIC_GOOGLE_SITE_VERIFICATION=
TZ=Africa/Nairobi
PYTHONUNBUFFERED=1
```

Keep secrets in Coolify's environment manager, not in Git.

## Persistent Storage

Postgres must persist between deployments. The repository Compose file uses:

```text
/opt/postgres-data:/var/lib/postgresql/data
```

OpenStudy file storage uses:

```text
/opt/courses:/opt/courses
```

In Coolify, ensure equivalent persistent storage exists for both paths or map
them to Coolify-managed volumes.

## Deployment Flow

```mermaid
flowchart TD
    Push["Push Git branch"] --> Coolify["Coolify deploys Compose app"]
    Coolify --> Migrations["App deploy flow runs migrations"]
    Migrations --> Health["Check app health"]
    Health --> SeedDryRun["Run curriculum seed dry-run"]
    SeedDryRun --> SeedApply["Run curriculum seed"]
    SeedApply --> Verify["Verify dashboard at custom domain"]
```

## Manual Curriculum Seed

After Coolify deploys successfully and migrations have run, open a shell in the
`openstudy` container and run:

```bash
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
```

Review the output. For an empty database, expect roughly:

```text
Courses: create=1, update=0
Sources: create=7, update=0
Assets: create=6, update=0
Tracks: create=1, update=0
Modules: create=16, update=0
Lessons/tasks: create=113, update=0
```

Then apply:

```bash
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose
```

Repeat runs should show updates instead of creates.

Production images include the generated manifest and the whitelisted flashcard
asset files. They do not include full source submodules, so production seeding
should use `--skip-generate`.

## Public Routing

The working Coolify setup keeps the domain attached only to the `frontend`
service:

```text
https://learn.alexmbugua.me
```

The frontend service should keep:

```yaml
expose:
  - "80"
labels:
  - "traefik.http.services.frontend.loadbalancer.server.port=80"
```

Do not add host `ports:` mappings for the public site in Coolify. Do not attach
the domain to `openstudy` or `postgres`.

### Networking

Coolify Compose applications should let Coolify create and attach the deployment
network. Do not define custom Compose networks for this deployment. Custom
networks can cause Traefik to choose the wrong container network address, which
can present as a public `504 Gateway Timeout` even when the frontend is healthy
inside the app network.

The final working shape is:

- no service-level `networks:` entries
- no top-level `networks:` block
- `frontend` exposes internal port `80`
- `frontend` has the Traefik load balancer port label
- `postgres` has no public route
- `openstudy` has no public route; frontend proxies API traffic to it

## Updating The Curriculum

1. Update the curated manifest, generator, source repos, or tests locally.
2. Regenerate and validate.
3. Commit and push.
4. Let Coolify deploy the new image.
5. Run the seed helper dry-run in the `openstudy` container.
6. Apply the seed if the counts look correct.

## Logs And Troubleshooting

### App Health Fails

Check backend logs and database connectivity. The curriculum seed should wait
until app deployment and migrations are healthy.

### Public Site Returns 504 Gateway Timeout

Verify the domain is attached to the `frontend` service, not the backend. Check
that `docker-compose.yml` has no custom `networks:` block and no service-level
network assignments. Keep `frontend` on internal port `80` via `expose` and the
Traefik service-port label.

### Seed Helper Cannot Find Python

Run it in the `openstudy` container, not the frontend container.

### Seed Helper Reports Missing DB Env

The shell does not have `POSTGRES_USER`, `POSTGRES_PASSWORD`, or `POSTGRES_DB`.
Use the backend container shell where Coolify/Compose injects those variables,
or configure them in the Coolify shell environment.

### Manifest Generation Fails

Production containers normally use the prebuilt manifest with
`--skip-generate`; they do not include full source repositories or submodules.
Regenerate the manifest during development or CI, then redeploy the image.

### Assets Warn As Missing

Check that the packaged image contains the whitelisted flashcard files under
`/app/curriculum/sources`. Full source repositories are still intentionally
excluded.

## Common Mistakes

- Running the helper from the frontend container.
- Losing Postgres data because the database path is not persistent.
- Updating a source submodule locally but not pushing the submodule pointer.
- Applying the seed without checking dry-run counts.
- Assuming Coolify rollback reverses database seed changes.
