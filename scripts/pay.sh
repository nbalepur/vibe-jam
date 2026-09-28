#!/bin/bash
# Copy payment-sheet TSV to the clipboard:
#   #, Username, Email, Submissions, Post-Test?, Earned
# Usage: ./scripts/pay.sh
#        ./scripts/pay.sh --no-copy

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f "$ROOT/.env.prod" ]; then
  echo "Missing $ROOT/.env.prod (needs DATABASE_URL for prod)."
  exit 1
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is not installed."
  exit 1
fi

conda run -n helpful-coding python "$ROOT/scripts/copy_payment_sheet.py" "$@"
