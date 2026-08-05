# Coolify Deployment

This is the canonical runbook for deploying the current OpenStudy application
from Git with Coolify and the repository `docker-compose.yml`. The deployment
contains `postgres`, `openstudy`, and `frontend` services. Login requires an
operator email address and password.

Coolify builds and starts the Compose services, but it does not invoke
`deploy.sh`. Database migrations, the one-time storage migration, and operator
bootstrap are explicit post-deploy operations in Coolify.

## Service Shape

- Attach the public domain only to `frontend` on internal port `80`.
- Do not publish host ports for `frontend`, `openstudy`, or `postgres`.
- Do not add custom Compose networks. Let Coolify attach its managed network.
- Persist Postgres at `/opt/postgres-data:/var/lib/postgresql/data`.
- Persist course files at `/opt/courses:/opt/courses`.
- Keep all secrets in Coolify's environment manager, not in Git.

## Environment

Configure these values in Coolify before the first deployment.

### Database and login

```text
POSTGRES_USER=openstudy
POSTGRES_PASSWORD=<strong-random-password>
POSTGRES_DB=openstudy

OPERATOR_USER_ID=00000000-0000-0000-0000-000000000001
OPERATOR_EMAIL=you@example.com
OPERATOR_DISPLAY_NAME=Your Name
APP_PASSWORD_HASH=$argon2id$...
SESSION_SECRET=<persistent-random-secret>
```

Keep the default `OPERATOR_USER_ID` when upgrading an existing single-user
installation. The data migration assigns existing records to that UUID.

Generate `APP_PASSWORD_HASH` locally and paste the complete output into
Coolify:

```bash
uv run python -m app.tools.hashpw
```

The value received by the container must begin with `$argon2id$`. Coolify and
Compose escaping can differ by deployment configuration, so verify the actual
container environment after deployment instead of relying on how the value
looks in the UI.

Generate `SESSION_SECRET` once and retain it across deployments:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

### Public URLs and frontend identity

```text
PUBLIC_BASE_URL=https://your-domain.example
PUBLIC_URL=https://your-domain.example
PUBLIC_SITE_URL=https://your-domain.example
PUBLIC_SITE_NAME=OpenStudy
PUBLIC_SHOW_LANDING=false
PUBLIC_GOOGLE_SITE_VERIFICATION=
CORS_ORIGINS=https://your-domain.example
```

`PUBLIC_BASE_URL` controls backend OAuth and MCP discovery. `PUBLIC_URL` keeps
email links compatible with upstream flows. `PUBLIC_SITE_*` values are baked
into the frontend image during the build.

### Secrets, signup, and email

```text
SECRETS_ENCRYPTION_KEY=<persistent-fernet-key>
SIGNUPS_ENABLED=false
EMAIL_BACKEND=console
EMAIL_FROM=hello@your-domain.example
EMAIL_FROM_NAME=OpenStudy
```

Generate the encryption key once and retain it. Changing it later makes
previously encrypted per-user credentials unreadable.

```bash
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

With `SIGNUPS_ENABLED=false`, only the seeded operator can sign in.
`EMAIL_BACKEND=console` writes verification and reset messages to backend logs.
For real delivery, configure:

```text
EMAIL_BACKEND=gmail_smtp
GMAIL_SMTP_USER=you@gmail.com
GMAIL_SMTP_APP_PASSWORD=<gmail-app-password>
EMAIL_FROM=you@gmail.com
```

### Curriculum and runtime

```text
OPENSTUDY_PACKAGED_CURRICULUM=1
PACKAGED_CURRICULUM_ASSETS_ROOT=/app/curriculum/sources
TZ=Africa/Nairobi
PYTHONUNBUFFERED=1
```

## First Deployment

1. Configure the environment and both persistent mounts.
2. Deploy the Compose application in Coolify.
3. Open a terminal in the `openstudy` backend container.
4. Run database migrations:

   ```bash
   uv run --no-sync python scripts/run_migrations.py
   ```

5. Move existing single-user course files into the operator's user-scoped
   directory. This command is idempotent and writes
   `/opt/courses/.phase1_migrated`:

   ```bash
   OPERATOR_USER_ID=00000000-0000-0000-0000-000000000001 STUDY_ROOT=/opt/courses bash scripts/migrate_study_root.sh
   ```

6. Create or reconcile the operator login:

   ```bash
   uv run --no-sync python scripts/seed_operator_password.py
   ```

7. Restart the `openstudy` backend and `frontend` services in Coolify.
8. Sign in with `OPERATOR_EMAIL` and the plaintext password used to generate
   `APP_PASSWORD_HASH`.

The operator seed is safe to run repeatedly. It reconciles email and display
name, but writes `APP_PASSWORD_HASH` only when the database password is `NULL`.
It deliberately does not replace an established password.

## Deployment Verification

- `/api/health` reports a healthy database and storage layer.
- Login succeeds with the configured operator email and plaintext password.
- Existing courses, tasks, topics, and files remain visible.
- `/opt/courses/.phase1_migrated` exists after an upgrade from single-user
  storage.
- The backend sees `PUBLIC_BASE_URL` as the external HTTPS origin.
- Postgres and course files survive another redeploy.

To inspect the injected password hash without printing it, run this in the
backend container as one physical line:

```bash
uv run --no-sync python -c 'import os; h=os.getenv("APP_PASSWORD_HASH",""); print({"set":bool(h),"prefix":h[:10],"length":len(h),"double_dollar":h.startswith("$$")})'
```

A normal Argon2id hash reports prefix `$argon2id$`, length `97`, and
`double_dollar: False`.

## Updating an Existing Operator Password

Changing `APP_PASSWORD_HASH` in Coolify and rerunning
`seed_operator_password.py` does not replace an existing database password.
If the script reports `already matches .env - nothing to do`, that behavior is
expected.

First verify that the plaintext password matches the injected hash.

```python
uv run --no-sync python -c 'import getpass,os; from argon2 import PasswordHasher; print("Hash matches password:",PasswordHasher().verify(os.environ["APP_PASSWORD_HASH"],getpass.getpass("Password: ")))'
```

OR REPL sequence below.

```bash
uv run --no-sync python
```

```python
import os,getpass
from argon2 import PasswordHasher as P
h=os.environ["APP_PASSWORD_HASH"]
p=getpass.getpass("Password: ")
P().verify(h,p)
```

The final expression must return `True`. Then exit Python with `exit()`.

To replace the stored password without requiring `psql` in the slim backend
image, start Python again and use the existing psycopg dependency:

```python
uv run --no-sync python -c 'import os,psycopg;c=psycopg.connect(host=os.getenv("PGHOST","postgres"),port=os.getenv("PGPORT","5432"),dbname=os.environ["POSTGRES_DB"],user=os.environ["POSTGRES_USER"],password=os.environ["POSTGRES_PASSWORD"]); r=c.execute("UPDATE users SET password_hash=%s WHERE id=%s RETURNING email",(os.environ["APP_PASSWORD_HASH"],"00000000-0000-0000-0000-000000000001")).fetchone(); c.commit(); print("Updated operator:",r[0] if r else "NOT FOUND"); c.close()'
```

OR REPL sequence below.

```bash
uv run --no-sync python
```

Enter each statement separately at the `>>>` prompt:

```python
import os,psycopg
e=os.environ
k={"host":e.get("PGHOST","postgres")}
k["dbname"]=e["POSTGRES_DB"]
k["user"]=e["POSTGRES_USER"]
k["password"]=e["POSTGRES_PASSWORD"]
c=psycopg.connect(**k)
q="UPDATE users SET password_hash=%s WHERE id=%s RETURNING email"
r=c.execute(q,(e["APP_PASSWORD_HASH"],e["OPERATOR_USER_ID"])).fetchone()
c.commit()
print("Updated operator:",r[0] if r else "NOT FOUND")
c.close()
exit()
```

Restart the backend, then sign in with the printed email and the plaintext
password. Never enter the Argon2 hash itself into the login form.

## Curriculum Seed

After migrations and operator bootstrap succeed, preview the curriculum import
inside the backend container:

```bash
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
```

Review the counts, then apply it:

```bash
scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose
```

Production images contain the prebuilt manifest and referenced flashcard
assets, not full source submodules, so production seeding uses
`--skip-generate`. Packaged assets are synchronized into:

```text
/opt/courses/<operator-user-id>/interview-engineering/resources/flashcards/
```

## Routing and Networking

Attach the domain only to `frontend`. The service exposes internal port `80`
and carries the Traefik load-balancer port label. The frontend proxies API and
MCP traffic to `openstudy` on the Coolify-managed Compose network.

Custom Compose networks can make Traefik select the wrong container address,
presenting as `504 Gateway Timeout` even while the frontend is healthy. The
working shape has no service-level `networks:` entries and no top-level
`networks:` block.

## Troubleshooting

### Login reports an incorrect password

1. Verify the injected hash prefix and length.
2. Verify the plaintext password against that hash with the Python REPL.
3. Remember that the seed script does not overwrite a non-null database hash.
4. Use the password replacement procedure above, restart the backend, and log
   in with the operator email returned by the update.

### Backend reports missing database variables

Run commands in the `openstudy` container, where Coolify injects
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

### Public site returns 504

Attach the domain to `frontend`, retain internal port `80`, and remove custom
networks or public backend/database routes.

### Existing files are missing

Confirm `/opt/courses/.phase1_migrated` exists and inspect
`/opt/courses/<operator-user-id>/`. Run the idempotent storage migration if the
old course directories still sit directly under `/opt/courses`.

### Flashcards are missing

Confirm `OPENSTUDY_PACKAGED_CURRICULUM=1`, verify assets exist beneath
`/app/curriculum/sources`, and inspect the user-scoped flashcard path under
`/opt/courses/<operator-user-id>/`. Restarting the backend reruns packaged asset
synchronization.

## Updating and Rollback

For later application updates, deploy the new image, run
`scripts/run_migrations.py`, restart services, verify health, and apply a
curriculum seed only when the manifest changed. Migration and seed operations
are idempotent.

A Coolify container rollback does not reverse database migrations, password
changes, curriculum imports, or file moves. Back up Postgres and `/opt/courses`
before material production changes.
