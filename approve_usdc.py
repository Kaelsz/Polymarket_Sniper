"""Approve Polymarket exchange contracts to spend USDC.e."""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
import json
from web3 import Web3

RPC = "https://rpc.ankr.com/polygon"
w3 = Web3(Web3.HTTPProvider(RPC))

PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY")
WALLET = Web3.to_checksum_address(os.getenv("POLYMARKET_ADDRESS"))

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")

EXCHANGE_CONTRACTS = [
    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
]

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

MAX_UINT256 = 2**256 - 1

usdc = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)

print(f"=== USDC.e Approval for Polymarket ===\n")
print(f"Wallet: {WALLET}")

bal = usdc.functions.balanceOf(WALLET).call()
print(f"USDC.e balance: ${bal / 1e6:.2f}")

pol = w3.eth.get_balance(WALLET)
print(f"POL balance: {w3.from_wei(pol, 'ether'):.4f}")
print()

nonce = w3.eth.get_transaction_count(WALLET)

for addr in EXCHANGE_CONTRACTS:
    spender = Web3.to_checksum_address(addr)
    current = usdc.functions.allowance(WALLET, spender).call()
    print(f"Contract {addr[:10]}... allowance: {current}")

    if current > 0:
        print(f"  Already approved, skipping.")
        continue

    print(f"  Approving...")
    tx = usdc.functions.approve(spender, MAX_UINT256).build_transaction({
        "from": WALLET,
        "nonce": nonce,
        "gasPrice": w3.eth.gas_price,
        "chainId": 137,
    })
    tx["gas"] = w3.eth.estimate_gas(tx)

    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  TX sent: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    status = "OK" if receipt["status"] == 1 else "FAILED"
    print(f"  {status} (gas used: {receipt['gasUsed']})")
    nonce += 1

print("\n=== Verification ===")
for addr in EXCHANGE_CONTRACTS:
    spender = Web3.to_checksum_address(addr)
    current = usdc.functions.allowance(WALLET, spender).call()
    tag = "OK" if current > 0 else "FAILED"
    print(f"  {addr[:10]}... [{tag}]")

print("\nDone! Now run: python3 setup_allowance.py")
