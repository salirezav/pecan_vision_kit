<<<<<<< HEAD
#!/usr/bin/env bash
# Copy models shipped in models/ into the dashboard library.
# Asks before copying unless --yes is passed. Does not replace a model
# that is already in the library.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash. Do not run it with sh or sudo sh." >&2
  exit 1
fi
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
if [[ -z "$PY" ]]; then
  echo "python3 is not installed." >&2
  exit 1
fi
export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/scripts/install_bundled_models.py" "$@"
=======
#!/usr/bin/env bash
# Copy models shipped in models/ into the dashboard library.
# Asks before copying unless --yes is passed. Does not replace a model
# that is already in the library.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "This script needs bash. Do not run it with sh or sudo sh." >&2
  exit 1
fi
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
if [[ -z "$PY" ]]; then
  echo "python3 is not installed." >&2
  exit 1
fi
export PYTHONPATH="$ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/scripts/install_bundled_models.py" "$@"
>>>>>>> 871aa392064a97663a2fda25a18dfd0b5b9317f7
