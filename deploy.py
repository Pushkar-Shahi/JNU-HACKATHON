"""Deploy IntegrityLedger to the local chain and anchor target_system/'s first hash.

    python deploy.py          # reset config.json to the baseline, then anchor it
    python deploy.py --keep   # anchor target_system/ exactly as it is now
"""
import json
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")  # macOS system Python

from pathlib import Path
from web3 import Web3

import target
from hash_utils import sha256_directory, directory_manifest
from ledger import DEPLOYMENT_FILE, ROOT, RPC_URL

ARTIFACT = ROOT / "artifacts" / "contracts" / "IntegrityLedger.sol" / "IntegrityLedger.json"
TARGET_DIR = ROOT / "target_system"
STATE_DIR = ROOT / ".state"
MANIFEST_PATH = STATE_DIR / "trusted_manifest.json"


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        sys.exit(f"No chain at {RPC_URL}. Start one first:  npx hardhat node")

    if not ARTIFACT.exists():
        print("Compiling contract…")
        subprocess.run(["npx", "hardhat", "compile", "--quiet"], cwd=ROOT, check=True)
    artifact = json.loads(ARTIFACT.read_text())

    # Hardhat's first two unlocked accounts play the two roles.
    dev, agent = w3.eth.accounts[0], w3.eth.accounts[1]

    # 1) Deploy (the deployer becomes the contract owner = developer).
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"])
    receipt = w3.eth.wait_for_transaction_receipt(factory.constructor().transact({"from": dev}))
    ledger = w3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])

    # 2) Ensure target_system/ has at least the baseline file.
    TARGET_DIR.mkdir(exist_ok=True)
    if "--keep" not in sys.argv:
        target.write_baseline()

    # 3) Anchor the DIRECTORY hash (not a single file) — matches what app.py monitors.
    genesis = sha256_directory(TARGET_DIR)
    if not genesis:
        sys.exit("target_system/ is empty. Nothing to anchor.")
    w3.eth.wait_for_transaction_receipt(ledger.functions.authorizeUpdate().transact({"from": dev}))
    w3.eth.wait_for_transaction_receipt(ledger.functions.updateHash(genesis).transact({"from": dev}))

    # 4) Save the trusted manifest so file-level diffs work immediately.
    STATE_DIR.mkdir(exist_ok=True)
    manifest = directory_manifest(TARGET_DIR)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    target.save_snapshot()

    DEPLOYMENT_FILE.write_text(json.dumps({
        "address": receipt.contractAddress,
        "deployBlock": receipt.blockNumber,
        "chainId": w3.eth.chain_id,
        "dev": dev,
        "agent": agent,
        "abi": artifact["abi"],
    }, indent=2))

    print(f"IntegrityLedger deployed at {receipt.contractAddress} (block {receipt.blockNumber}, chain {w3.eth.chain_id})")
    print(f"Genesis directory hash anchored: {genesis}")
    print(f"Wrote {DEPLOYMENT_FILE.name}")


if __name__ == "__main__":
    main()
