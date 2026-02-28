"""Derive CLOB API credentials from the wallet private key."""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
from eth_account import Account
from py_clob_client.client import ClobClient

host = "https://clob.polymarket.com"
key = os.getenv("POLY_PRIVATE_KEY")
address = os.getenv("POLYMARKET_ADDRESS")
sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "0"))
funder = os.getenv("POLY_FUNDER", "") or address

eoa = Account.from_key(key).address
print(f"EOA (from PK):     {eoa}")
print(f"Funder:            {funder}")
print(f"Signature type:    {sig_type}")
print(f"Host:              {host}")
print()

kwargs = {"key": key, "chain_id": 137}
if sig_type != 0:
    kwargs["signature_type"] = sig_type
if funder:
    kwargs["funder"] = funder

client = ClobClient(host, **kwargs)

print("Deriving API credentials...")
try:
    creds = client.create_or_derive_api_creds()
    print()
    print("=== ADD THESE TO YOUR .env ===")
    print()
    print(f"POLY_API_KEY={creds.api_key}")
    print(f"POLY_API_SECRET={creds.api_secret}")
    print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
    print()
    print("=== TEST: setting creds and checking balance... ===")
    client.set_api_creds(creds)

    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
    result = client.get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print(f"CLOB balance: {result}")
    print("Credentials set OK")
except Exception as e:
    print(f"FAILED: {e}")
