"""Approve Polymarket exchange contracts to spend USDC.e and Conditional Tokens."""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
import json
from web3 import Web3

RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://polygon.meowrpc.com",
]

w3 = None
for rpc in RPCS:
    try:
        _w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        _w3.eth.chain_id
        w3 = _w3
        print(f"Connected to {rpc}")
        break
    except Exception:
        continue

if w3 is None:
    print("ERROR: No working RPC found")
    exit(1)

PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY")
WALLET = Web3.to_checksum_address(os.getenv("POLYMARKET_ADDRESS"))

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

EXCHANGE_CONTRACTS = [
    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
]

ERC20_ABI = json.loads("""[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

CTF_ABI = json.loads("""[
    {"constant":false,"inputs":[{"name":"_operator","type":"address"},{"name":"_approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_operator","type":"address"}],"name":"isApprovedForAll","outputs":[{"name":"","type":"bool"}],"type":"function"}
]""")

MAX_UINT256 = 2**256 - 1

usdc = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)
ctf = w3.eth.contract(address=CTF, abi=CTF_ABI)

print(f"=== Polymarket Full Approval ===\n")
print(f"Wallet: {WALLET}")

bal = usdc.functions.balanceOf(WALLET).call()
print(f"USDC.e balance: ${bal / 1e6:.2f}")

pol = w3.eth.get_balance(WALLET)
print(f"POL balance: {w3.from_wei(pol, 'ether'):.4f}")

nonce = w3.eth.get_transaction_count(WALLET)

# --- USDC.e approvals ---
print(f"\n=== Step 1: USDC.e Approvals ===\n")
for addr in EXCHANGE_CONTRACTS:
    spender = Web3.to_checksum_address(addr)
    current = usdc.functions.allowance(WALLET, spender).call()
    print(f"{addr[:10]}... allowance: {'MAX (OK)' if current > 0 else '0 (NEED APPROVE)'}")
    if current > 0:
        continue
    tx = usdc.functions.approve(spender, MAX_UINT256).build_transaction({
        "from": WALLET, "nonce": nonce, "gasPrice": w3.eth.gas_price, "chainId": 137,
    })
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    print(f"  Approved! TX: {tx_hash.hex()[:20]}... gas: {receipt['gasUsed']}")
    nonce += 1

# --- Conditional Token approvals ---
print(f"\n=== Step 2: Conditional Token (CTF) Approvals ===\n")
for addr in EXCHANGE_CONTRACTS:
    operator = Web3.to_checksum_address(addr)
    approved = ctf.functions.isApprovedForAll(WALLET, operator).call()
    print(f"{addr[:10]}... approved: {'YES (OK)' if approved else 'NO (NEED APPROVE)'}")
    if approved:
        continue
    tx = ctf.functions.setApprovalForAll(operator, True).build_transaction({
        "from": WALLET, "nonce": nonce, "gasPrice": w3.eth.gas_price, "chainId": 137,
    })
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    print(f"  Approved! TX: {tx_hash.hex()[:20]}... gas: {receipt['gasUsed']}")
    nonce += 1

print(f"\n=== All Done! Now test: python3 debug_api.py ===")
