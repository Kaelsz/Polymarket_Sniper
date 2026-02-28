"""Set up CLOB allowance so the bot can trade."""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

host = "https://clob.polymarket.com"
key = os.getenv("POLY_PRIVATE_KEY")

client = ClobClient(host, key=key, chain_id=137)

api_key = os.getenv("POLY_API_KEY")
api_secret = os.getenv("POLY_API_SECRET")
api_passphrase = os.getenv("POLY_API_PASSPHRASE")

if len(api_secret) % 4:
    api_secret += "=" * (4 - len(api_secret) % 4)

creds = ApiCreds(
    api_key=api_key,
    api_secret=api_secret,
    api_passphrase=api_passphrase,
)
client.set_api_creds(creds)

print("=== Balance & Allowance Setup ===\n")

print("[1] Checking current balance/allowance...")
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    print(f"    Result: {result}")
except Exception as e:
    print(f"    Error: {e}")

print("\n[2] Updating allowance...")
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.update_balance_allowance(params)
    print(f"    Result: {result}")
except Exception as e:
    print(f"    Error: {e}")

print("\n[3] Checking again after update...")
try:
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    result = client.get_balance_allowance(params)
    print(f"    Result: {result}")
except Exception as e:
    print(f"    Error: {e}")

print("\nDone!")
