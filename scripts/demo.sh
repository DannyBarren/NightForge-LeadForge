#!/usr/bin/env bash
#
# NightForge live-demo driver.
#
#   1. Dry-run (validates config + API keys, no LLM calls)
#   2. Sample run (3 leads end-to-end, guaranteed output even if research fails)
#   3. Prints the newest CSV path + estimated cost
#
# Usage:  ./scripts/demo.sh   (or)   make demo
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"

hr() { printf '%.0s=' {1..60}; printf '\n'; }

hr
echo "NightForge demo  —  step 1/3: dry-run (config + preflight)"
hr
"$PY" scripts/run_overnight.py --dry-run
DRY_RC=$?
if [ "$DRY_RC" -ne 0 ]; then
  echo
  echo "Dry-run preflight failed (exit $DRY_RC). Fix the ERROR line(s) above."
  echo "Most commonly: set OPENAI_API_KEY in .env, then re-run ./scripts/demo.sh"
  exit "$DRY_RC"
fi

echo
hr
echo "NightForge demo  —  step 2/3: sample run (3 leads end-to-end)"
hr
"$PY" scripts/run_overnight.py --sample --log-level WARNING
SAMPLE_RC=$?

echo
hr
echo "NightForge demo  —  step 3/3: newest output"
hr
NEWEST_CSV="$(ls -t data/output/leads_*.csv 2>/dev/null | head -n 1)"
if [ -n "$NEWEST_CSV" ]; then
  ROWS="$("$PY" - "$NEWEST_CSV" <<'PYEOF'
import csv, sys
try:
    with open(sys.argv[1], newline="", encoding="utf-8") as f:
        print(sum(1 for _ in csv.DictReader(f)))
except Exception:
    print(0)
PYEOF
)"
  echo "Newest CSV:  $NEWEST_CSV  (${ROWS} lead row(s))"
  NEWEST_TOKENS="$(ls -t data/output/token_usage_*.json 2>/dev/null | head -n 1)"
  if [ -n "$NEWEST_TOKENS" ]; then
    COST="$("$PY" - "$NEWEST_TOKENS" <<'PYEOF'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    print(f"${data.get('estimated_cost_usd', 0):.4f}")
except Exception:
    print("unknown")
PYEOF
)"
    echo "Estimated cost:  $COST"
    echo "Cost log:  $NEWEST_TOKENS"
  fi
  echo
  echo "Open the leads in the CSV above, or launch the UI with:  streamlit run app.py"
else
  echo "No CSV produced. Review the output above (check OPENAI_API_KEY / search quotas)."
  exit "${SAMPLE_RC:-1}"
fi

exit 0
