#!/usr/bin/env bash
# Regenerate, validate, and seed the OpenStudy curriculum manifest.
#
# Intended for manual server-side use after deployment and DB migrations:
#   scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
#   scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose

set -Eeuo pipefail

CURRENT_STEP="startup"
STARTED_AT=$SECONDS

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_MANIFEST="curriculum/interview_manifest.v2.yaml"

MANIFEST="$DEFAULT_MANIFEST"
DRY_RUN=0
SKIP_GENERATE=0
SKIP_VALIDATE=0
FORCE=1
VERBOSE=0
RUNTIME="host"
AUTO_SKIP_GENERATE=0

if command -v tput >/dev/null 2>&1 && [ -t 1 ]; then
    BOLD="$(tput bold)"
    DIM="$(tput dim)"
    RED="$(tput setaf 1)"
    YELLOW="$(tput setaf 3)"
    GREEN="$(tput setaf 2)"
    RESET="$(tput sgr0)"
else
    BOLD=""
    DIM=""
    RED=""
    YELLOW=""
    GREEN=""
    RESET=""
fi

usage() {
    cat <<'USAGE'
Usage:
  scripts/curriculum/deploy_seed_openstudy.sh [options]

Options:
  --dry-run          Generate, validate, and run seed dry-run only.
  --skip-generate   Skip create_manifest.py and use the prebuilt manifest.
  --skip-validate   Skip validate_manifest.py.
  --force           Pass --force to create_manifest.py. Enabled by default.
  --verbose         Enable verbose output throughout.
  --manifest PATH   Override manifest path.
  -h, --help        Show this help.

Production containers typically use:
  scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --dry-run --verbose
  scripts/curriculum/deploy_seed_openstudy.sh --skip-generate --verbose

Manifests are expected to be prebuilt during development or CI and included in
the image. Production images do not need curriculum/sources or .git metadata;
when sources are absent, validation runs in manifest-only mode.
USAGE
}

log() {
    printf '%s\n' "$*"
}

warn() {
    printf '%sWarning:%s %s\n' "$YELLOW" "$RESET" "$*" >&2
}

die() {
    printf '%sError:%s %s\n' "$RED" "$RESET" "$*" >&2
    exit 1
}

on_error() {
    local exit_code=$?
    printf '%sFailed during step:%s %s (exit %s)\n' \
        "$RED" "$RESET" "$CURRENT_STEP" "$exit_code" >&2
    exit "$exit_code"
}

trap on_error ERR

run_cmd() {
    if [ "$VERBOSE" -eq 1 ]; then
        printf '%s$%s' "$DIM" "$RESET"
        printf ' %q' "$@"
        printf '\n'
    fi
    "$@"
}

run_step() {
    local label="$1"
    shift
    CURRENT_STEP="$label"
    local step_start=$SECONDS
    log
    log "${BOLD}${label}${RESET}"
    "$@"
    local elapsed=$((SECONDS - step_start))
    log "${GREEN}Completed:${RESET} ${label} (${elapsed}s)"
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dry-run)
                DRY_RUN=1
                ;;
            --skip-generate)
                SKIP_GENERATE=1
                ;;
            --skip-validate)
                SKIP_VALIDATE=1
                ;;
            --force)
                FORCE=1
                ;;
            --verbose)
                VERBOSE=1
                ;;
            --manifest)
                [ "$#" -ge 2 ] || die "--manifest requires a path"
                MANIFEST="$2"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "unknown flag: $1"
                ;;
        esac
        shift
    done
}

python_bin() {
    if [ -n "${PYTHON:-}" ]; then
        printf '%s\n' "$PYTHON"
        return
    fi
    if command -v python >/dev/null 2>&1; then
        printf '%s\n' "python"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' "python3"
        return
    fi
    die "python is not available on PATH"
}

require_file() {
    [ -f "$1" ] || die "required file missing: $1"
}

detect_runtime() {
    if [ -f /.dockerenv ]; then
        RUNTIME="container"
    elif grep -qaE '/docker/|/kubepods/|/containerd/' /proc/1/cgroup 2>/dev/null; then
        RUNTIME="container"
    else
        RUNTIME="host"
    fi
}

require_db_env() {
    local missing=()
    local name
    for name in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB; do
        if [ -z "${!name:-}" ]; then
            missing+=("$name")
        fi
    done

    if [ "${#missing[@]}" -gt 0 ]; then
        die "database environment missing: ${missing[*]}. Run inside the OpenStudy container or export DB env vars before seeding."
    fi
}

print_context() {
    log "${BOLD}OpenStudy curriculum seed helper${RESET}"
    log "Repo root: $REPO_ROOT"
    log "Manifest: $MANIFEST"
    log "Environment: ${APP_ENV:-${ENVIRONMENT:-${NODE_ENV:-unset}}}"
    log "Runtime: $RUNTIME"
    log "Dry-run mode: $([ "$DRY_RUN" -eq 1 ] && printf 'yes' || printf 'no')"
    log "Verbose: $([ "$VERBOSE" -eq 1 ] && printf 'yes' || printf 'no')"
    log "Generate manifest: $([ "$SKIP_GENERATE" -eq 1 ] && printf 'no' || printf 'yes')"

    if [ "$RUNTIME" = "host" ]; then
        warn "not running inside a detected container; ensure DB environment variables point at the deployed database"
    elif [ "$AUTO_SKIP_GENERATE" -eq 1 ]; then
        warn "container runtime detected without curriculum/sources; using prebuilt manifest and skipping generation"
    elif [ "$SKIP_GENERATE" -eq 0 ]; then
        warn "container runtime detected; production usually uses --skip-generate with the prebuilt manifest"
    fi
}

preflight() {
    cd "$REPO_ROOT"

    PYTHON_BIN="$(python_bin)"
    export PYTHON_BIN

    require_file "app/main.py"
    require_file "scripts/curriculum/validate_manifest.py"
    require_file "scripts/curriculum/seed_openstudy.py"
    if [ "$SKIP_GENERATE" -eq 0 ]; then
        require_file "scripts/curriculum/create_manifest.py"
    else
        require_file "$MANIFEST"
    fi

    log "Python: $("$PYTHON_BIN" --version 2>&1)"
}

generate_manifest() {
    if [ "$SKIP_GENERATE" -eq 1 ]; then
        log "Skipping manifest generation."
        return
    fi

    local cmd=("$PYTHON_BIN" "scripts/curriculum/create_manifest.py" "--output" "$MANIFEST")
    if [ "$FORCE" -eq 1 ]; then
        cmd+=("--force")
    fi
    if [ "$VERBOSE" -eq 1 ]; then
        cmd+=("--verbose")
    fi
    run_cmd "${cmd[@]}"
}

validate_manifest() {
    if [ "$SKIP_VALIDATE" -eq 1 ]; then
        log "Skipping manifest validation."
        return
    fi

    local cmd=("$PYTHON_BIN" "scripts/curriculum/validate_manifest.py" "$MANIFEST")
    if [ ! -d "$REPO_ROOT/curriculum/sources" ]; then
        cmd+=("--allow-missing-source-files")
    fi
    run_cmd "${cmd[@]}"
}

seed_dry_run() {
    require_db_env
    local cmd=("$PYTHON_BIN" "scripts/curriculum/seed_openstudy.py" "--manifest" "$MANIFEST" "--dry-run" "--verbose")
    if [ ! -d "$REPO_ROOT/curriculum/sources" ]; then
        cmd+=("--allow-missing-source-files")
    fi
    run_cmd "${cmd[@]}"
}

seed_apply() {
    if [ "$DRY_RUN" -eq 1 ]; then
        log "Dry-run mode active; skipping real seed."
        return
    fi

    require_db_env
    local cmd=("$PYTHON_BIN" "scripts/curriculum/seed_openstudy.py" "--manifest" "$MANIFEST")
    if [ ! -d "$REPO_ROOT/curriculum/sources" ]; then
        cmd+=("--allow-missing-source-files")
    fi
    if [ "$VERBOSE" -eq 1 ]; then
        cmd+=("--verbose")
    fi
    run_cmd "${cmd[@]}"
}

main() {
    parse_args "$@"
    detect_runtime
    if [ "$RUNTIME" = "container" ] && [ "$SKIP_GENERATE" -eq 0 ] && [ ! -d "$REPO_ROOT/curriculum/sources" ]; then
        SKIP_GENERATE=1
        AUTO_SKIP_GENERATE=1
    fi
    print_context
    preflight

    run_step "[1/4] Generating curriculum manifest..." generate_manifest
    run_step "[2/4] Validating curriculum manifest..." validate_manifest
    run_step "[3/4] Running seed dry-run..." seed_dry_run
    run_step "[4/4] Applying curriculum seed..." seed_apply

    local elapsed=$((SECONDS - STARTED_AT))
    log
    log "${GREEN}Curriculum seed helper finished in ${elapsed}s.${RESET}"
}

main "$@"
