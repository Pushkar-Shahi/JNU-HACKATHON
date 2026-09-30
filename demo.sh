#!/usr/bin/env bash
# One-command demo: local chain -> deploy -> dashboard. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")"

[ -d node_modules ] || npm install --no-fund --no-audit
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
mkdir -p .state target_system
export PYTHONWARNINGS="ignore:urllib3 v2 only supports OpenSSL"  # macOS system Python noise
export DEVELOPER_PASSWORD="${DEVELOPER_PASSWORD:-Axion}"

npx hardhat node > .state/chain.log 2>&1 &
CHAIN_PID=$!
# Stop the chain AND the dashboard however this script ends (Ctrl-C, kill, crash).
trap 'trap - EXIT INT TERM; kill 0 2>/dev/null' EXIT INT TERM

echo -n "Starting local blockchain"
until curl -s -o /dev/null -X POST -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' http://127.0.0.1:8545; do
  kill -0 $CHAIN_PID 2>/dev/null || { echo; echo "Chain failed to start, see .state/chain.log"; exit 1; }
  echo -n "."; sleep 0.5
done
echo " up"

npx hardhat compile --quiet
.venv/bin/python deploy.py
# Background + wait (not foreground) so a kill/Ctrl-C runs the cleanup trap immediately.
.venv/bin/streamlit run app.py --server.port 8501 &
wait $!
