"""Check on-chain USDC balance and approve exchange contracts."""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
import json
from web3 import Web3

POLYGON_RPC = "https://polygon-rpc.com"
WALLET = os.getenv("POLYMARKET_ADDRESS")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (bridged)
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC

ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},{"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"}]')

w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

print(f"=== Wallet Check: {WALLET} ===\n")

pol_balance = w3.eth.get_balance(WALLET)
print(f"POL (gas): {w3.from_wei(pol_balance, 'ether'):.4f} POL")

for name, addr in [("USDC.e (bridged)", USDC_E), ("USDC (native)", USDC_NATIVE)]:
    contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
    try:
        bal = contract.functions.balanceOf(Web3.to_checksum_address(WALLET)).call()
        decimals = contract.functions.decimals().call()
        human = bal / (10 ** decimals)
        print(f"{name}: ${human:.6f} (raw: {bal})")
    except Exception as e:
        print(f"{name}: error - {e}")

print()
if pol_balance == 0:
    print("WARNING: No POL for gas! Send ~0.5 POL to this address.")
    print("Without gas, the bot cannot approve contracts or trade.")
