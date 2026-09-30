# Decentralized Runtime Integrity Engine

Continuously fingerprints `target_system/config.json` (SHA-256), anchors the trusted
hash on a local blockchain, and tells an **approved developer release** apart from
**unauthorized tampering**, live.

| State | Condition | What the agent does |
|---|---|---|
| 🟢 **Verified** | live hash == on-chain hash | nothing |
| 🔵 **Authorized update** | hashes differ, developer called `authorizeUpdate()` first | anchors the new hash via `updateHash()`, which consumes the approval |
| 🔴 **Unauthorized modification** | hashes differ, no approval | writes a permanent `TamperDetected` alert on-chain |

## Run it

One command (starts chain, deploys, opens dashboard; Ctrl-C stops everything):

```bash
./demo.sh
```

Or step by step, in three terminals:

```bash
npm install                                  # Hardhat
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
npx hardhat node                             # terminal 1: local chain on :8545 (chain id 31337)
```

```bash
npx hardhat compile && .venv/bin/python deploy.py   # terminal 2: deploy + anchor genesis hash
.venv/bin/streamlit run app.py                      # dashboard on http://localhost:8501
```

`deploy.py` resets `config.json` to the baseline before anchoring it, so every demo
starts clean. Use `deploy.py --keep` to anchor the file as-is. If you restart the
chain, re-run `deploy.py`; the dashboard shows "Contract not deployed" until you do.

## Demo script (≈90 seconds)

1. **Green.** "Every second the agent hashes the config and compares it with the hash on-chain."
2. **Simulate hack.** The attacker redirects the payment gateway, disables MFA, and adds a backdoor admin.
   The dashboard turns red, shows exactly which fields changed, and the alert is written on-chain.
3. **Approve update while red.** It's refused: an approval can't retroactively bless a tampered file.
4. **Reset.** The file is restored from the last trusted snapshot and the dashboard goes back to green.
5. **Approve update.** The developer key signs `authorizeUpdate()`, the release lands, the agent anchors
   the new hash and the dashboard goes blue. The approval is single-use, so the next edit is tampering again.
6. **Bonus.** Edit `target_system/config.json` in your IDE or delete it: both show red within a second.

## Files

| File | Role |
|---|---|
| `contracts/IntegrityLedger.sol` | trusted hash, one-shot approval flag, event audit trail |
| `deploy.py` | deploys the contract, anchors the genesis hash, writes `deployment.json` |
| `hash_utils.py` | streaming SHA-256 of the monitored file |
| `ledger.py` | web3.py wrapper: reads, writes, audit trail from contract events |
| `target.py` | the monitored config plus the hack, release and restore scenarios |
| `app.py` | monitoring agent + Streamlit dashboard (3-state engine, 1s polling) |
| `styles.py` | dashboard CSS and icons |

## Trust model notes (for judges' questions)

- **Two keys.** Only the owner (developer) can approve. The agent can only commit a hash inside an open
  approval window, and each approval covers exactly one hash.
- **Tamper-evident.** Every approval, anchor and tamper alert is an event on-chain. The dashboard's audit
  trail is read straight from those logs, not from a local database.
- **Demo shortcuts.** Hardhat's unlocked accounts stand in for the dev and agent wallets, and the agent
  runs inside the dashboard process. In production the agent would be a separate daemon with its own key,
  and approval would come from a hardware wallet or a multisig.
# JNU-HACKATHON-
# JNU-HACKATHON
