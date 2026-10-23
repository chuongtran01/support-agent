# Shared by the hooks. Sourced, not run.
#
# CHANGED holds the paths a hook is about (staged files, or the files in the commits being
# pushed), one per line. touches_backend / touches_frontend say whether any is on that side.

ROOT="$(git rev-parse --show-toplevel)"
BACKEND_BIN="$ROOT/backend/.venv/bin"

touches_backend() { printf '%s\n' "$CHANGED" | grep -qE '^(backend|data|evals)/'; }
touches_frontend() { printf '%s\n' "$CHANGED" | grep -q '^frontend/'; }

# run "what it checks" command...   Prints the command's output only if it fails.
run() {
  label="$1"
  shift
  printf '  %s ... ' "$label"
  if output="$("$@" 2>&1)"; then
    echo "ok"
  else
    echo "FAILED"
    printf '%s\n' "$output" | tail -n 40
    echo
    echo "$HOOK blocked this. Fix it, or skip the hook once with --no-verify."
    exit 1
  fi
}

need_backend() {
  if [ ! -x "$BACKEND_BIN/ruff" ]; then
    echo "backend/.venv is missing: run 'make install' first."
    exit 1
  fi
}

need_frontend() {
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    echo "frontend/node_modules is missing: run 'make frontend-install' first."
    exit 1
  fi
}
