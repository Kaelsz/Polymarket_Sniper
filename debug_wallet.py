"""
Diagnostic complet: identifie le bon signature_type et funder
pour que le CLOB voit le balance USDC.e.
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
import json
from eth_account import Account
from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

pk = os.getenv("POLY_PRIVATE_KEY")
env_addr = os.getenv("POLYMARKET_ADDRESS")

eoa = Account.from_key(pk).address
print("=" * 60)
print("WALLET DIAGNOSTIC")
print("=" * 60)
print(f"POLYMARKET_ADDRESS (.env):  {env_addr}")
print(f"EOA (derived from PK):      {eoa}")
match = env_addr.lower() == eoa.lower()
print(f"Match: {'YES' if match else 'NO - DIFFERENT ADDRESSES!'}")
print()

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
        break
    except Exception:
        continue

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ERC20_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')

if w3:
    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    for label, addr in [("EOA", eoa), (".env address", env_addr)]:
        try:
            bal = usdc.functions.balanceOf(Web3.to_checksum_address(addr)).call()
            print(f"USDC.e at {label} ({addr[:10]}...): ${bal / 1e6:.2f}")
        except Exception as e:
            print(f"USDC.e at {label}: error - {e}")
    print()

print("=" * 60)
print("TESTING CLOB WITH DIFFERENT signature_type / funder")
print("=" * 60)

host = "https://clob.polymarket.com"

configs = [
    {"label": "sig=0 (EOA), no funder", "sig": 0, "funder": None},
    {"label": "sig=0 (EOA), funder=.env addr", "sig": 0, "funder": env_addr},
    {"label": "sig=2 (Gnosis), funder=.env addr", "sig": 2, "funder": env_addr},
    {"label": "sig=1 (Proxy), funder=.env addr", "sig": 1, "funder": env_addr},
]

if not match:
    configs.append({"label": "sig=0 (EOA), funder=EOA", "sig": 0, "funder": eoa})
    configs.append({"label": "sig=2 (Gnosis), funder=EOA", "sig": 2, "funder": eoa})

for cfg in configs:
    print(f"\n--- {cfg['label']} ---")
    try:
        kwargs = {"key": pk, "chain_id": 137}
        if cfg["sig"] != 0:
            kwargs["signature_type"] = cfg["sig"]
        if cfg["funder"]:
            kwargs["funder"] = cfg["funder"]

        client = ClobClient(host, **kwargs)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)

        result = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        bal = result.get("balance", "?") if isinstance(result, dict) else getattr(result, "balance", "?")
        print(f"  CLOB balance: {bal}")

        if str(bal) != "0":
            print(f"  >>> FOUND NON-ZERO BALANCE! This config works!")
            print(f"  >>> signature_type={cfg['sig']}, funder={cfg['funder']}")

    except Exception as e:
        err_str = str(e)
        if "401" in err_str:
            print(f"  Auth failed (401)")
        elif "403" in err_str:
            print(f"  Geo-blocked (403) - auth OK")
        else:
            print(f"  Error: {err_str[:120]}")

print("\n" + "=" * 60)
print("DONE")
print("=" * 60)
