#!/usr/bin/env bash
set -euo pipefail
CONFIG_ROOT="$PWD/.circleci"
SOURCE_ROOT="$PWD/source-linux"
ARTIFACTS=/tmp/heifang-linux-verification
TOOLCHAIN=/tmp/heifang-linux-toolchain
export ARTIFACTS
mkdir -p "$ARTIFACTS/logs" "$ARTIFACTS/junit" "$ARTIFACTS/test-plugin" "$TOOLCHAIN/bin"
trap 'printf "%s\n" "$?" > "$ARTIFACTS/runner-exit-code.txt"' EXIT
failures=0
last_status=0

# Record every gate's exit code; independent checks still run after lint/test failures.
step() {
  local name="$1"
  shift
  set +e
  "$@" 2>&1 | tee "$ARTIFACTS/logs/$name.log"
  local codes=("${PIPESTATUS[@]}")
  set -e
  last_status="${codes[0]}"
  if [[ "${codes[1]}" != 0 ]]; then last_status="${codes[1]}"; fi
  printf '%s\t%s\n' "$name" "$last_status" >> "$ARTIFACTS/stages.tsv"
  if [[ "$last_status" != 0 ]]; then failures=1; fi
}

step inputs python "$CONFIG_ROOT/ci_support.py" inputs "$SOURCE_COMMIT"
test "$last_status" -eq 0
test "$(uname -s)" = Linux
test "$(uname -m)" = x86_64
python -c 'import sys; assert sys.version_info[:2] == (3, 12)'

# Public toolchain downloads only; exact uv/Node/pnpm versions.
step uv-install python -m pip install --user --disable-pip-version-check --no-deps "uv==$UV_VERSION"
test "$last_status" -eq 0
export PATH="$TOOLCHAIN/bin:$HOME/.local/bin:$PATH"
test "$(uv --version | cut -d ' ' -f 2)" = "$UV_VERSION"
archive="node-v${NODE_VERSION}-linux-x64.tar.xz"
step node-download curl --fail --location --retry 3 --output "$TOOLCHAIN/$archive" "https://nodejs.org/dist/v${NODE_VERSION}/$archive"
test "$last_status" -eq 0
step node-checksums curl --fail --location --retry 3 --output "$TOOLCHAIN/SHASUMS256.txt" "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt"
test "$last_status" -eq 0
(cd "$TOOLCHAIN"; grep -F "  $archive" SHASUMS256.txt > node.sha256; sha256sum --check node.sha256)
cp "$TOOLCHAIN/node.sha256" "$ARTIFACTS/node.sha256"
tar -xJf "$TOOLCHAIN/$archive" -C "$TOOLCHAIN"
export PATH="$TOOLCHAIN/node-v${NODE_VERSION}-linux-x64/bin:$PATH"
test "$(node --version)" = "v$NODE_VERSION"
corepack enable --install-directory "$TOOLCHAIN/bin"
step pnpm-install corepack prepare "pnpm@$PNPM_VERSION" --activate
test "$last_status" -eq 0
test "$(pnpm --version)" = "$PNPM_VERSION"

# Published GitHub Ed25519 host key; no unverified ssh-keyscan or key contents in logs.
printf '%s\n' 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' > "$TOOLCHAIN/github-known-hosts"
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlgorithms=ssh-ed25519 -o UserKnownHostsFile=$TOOLCHAIN/github-known-hosts"
test ! -e "$SOURCE_ROOT"
git init --quiet "$SOURCE_ROOT"
git -C "$SOURCE_ROOT" remote add origin "git@github.com:${SOURCE_REPOSITORY}.git"
step source-fetch git -C "$SOURCE_ROOT" fetch --no-tags --depth=1 origin "$SOURCE_COMMIT"
test "$last_status" -eq 0
git -C "$SOURCE_ROOT" -c core.autocrlf=false checkout --detach "$SOURCE_COMMIT"
test "$(git -C "$SOURCE_ROOT" rev-parse HEAD)" = "$SOURCE_COMMIT"
test -z "$(git -C "$SOURCE_ROOT" status --porcelain)"
# Never allow a dotenv from a reused/modified checkout to enter tests.
test ! -e "$SOURCE_ROOT/apps/server/.env"
test ! -e "$SOURCE_ROOT/apps/server/.env.ci"

export CI=true REQUIRE_INTEGRATION_DB=1 UV_LOCKED=true UV_PYTHON_DOWNLOADS=never
export DATABASE_URL=postgresql+asyncpg://heifang:heifang@127.0.0.1:5432/heifang
export TEST_DATABASE_URL="$DATABASE_URL" HEIFANG_ENV=ci
SERVER="$SOURCE_ROOT/apps/server"
cd "$SERVER"
step dependencies uv sync --locked --all-extras --python 3.12
test "$last_status" -eq 0
step ripgrep uv run --locked --no-sync python scripts/fetch_ripgrep.py --install-server
test "$last_status" -eq 0
step ripgrep-version ./bin/rg --version
test "$last_status" -eq 0
export PATH="$SERVER/bin:$PATH"
cd "$SOURCE_ROOT"
step contract-dependencies pnpm install --frozen-lockfile --filter @heifang/contract-rest-types...
test "$last_status" -eq 0

# Temporary CI-only modules live in artifacts, outside the disposable source tree.
cp "$CONFIG_ROOT/description_release_testclock.py" "$CONFIG_ROOT/release_verification_plugin.py" "$CONFIG_ROOT/ci_support.py" "$CONFIG_ROOT/sitecustomize.py" "$ARTIFACTS/test-plugin/"
export PYTHONPATH="$ARTIFACTS/test-plugin" HEIFANG_VERIFY_OFFLINE=1
cd "$SERVER"
step runtime uv run --locked --no-sync python "$CONFIG_ROOT/ci_support.py" runtime
step postgres-ready uv run --locked --no-sync python "$CONFIG_ROOT/ci_support.py" wait-db
test "$last_status" -eq 0
step ruff uv run --locked --no-sync ruff check .
step mypy uv run --locked --no-sync mypy
step log-catalog uv run --locked --no-sync python scripts/sync_log_event_registry.py --check
step migrations uv run --locked --no-sync alembic upgrade head
step postgres-vector uv run --locked --no-sync python "$CONFIG_ROOT/ci_support.py" verify-db
step schema-live uv run --locked --no-sync python scripts/check_schema_gate.py --live
step pytest uv run --locked --no-sync pytest tests --tb=short -ra -o junit_family=xunit1 \
  -p description_release_testclock -p release_verification_plugin --junitxml="$ARTIFACTS/junit/backend.xml"
step pytest-report python "$CONFIG_ROOT/ci_support.py" reports "$ARTIFACTS"
cd "$SOURCE_ROOT"
step contracts node scripts/gen-types.mjs
cd "$SERVER"
step conformance-export uv run --locked --no-sync python -m heifang.conformance.export
cd "$SOURCE_ROOT"
step legal-mirror node scripts/sync-legal-md.mjs --check
step doc-pointers node scripts/check-doc-section-pointers.mjs
step drift git diff --exit-code
step untracked python "$CONFIG_ROOT/ci_support.py" clean "$SOURCE_ROOT"
exit "$failures"
