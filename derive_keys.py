"""Derive CLOB API credentials from the wallet private key."""

from dotenv import load_dotenv
load_dotenv(override=True)

import os
from py_clob_client.client import ClobClient

host = "https://clob.polymarket.com"
key = os.getenv("POLY_PRIVATE_KEY")
address = os.getenv("POLYMARKET_ADDRESS")

print(f"Address: {address}")
print(f"Host:    {host}")
print()

client = ClobClient(host, key=key, chain_id=137)

print("Deriving API credentials...")
try:
    creds = client.derive_api_key()
    print()
    print("=== ADD THESE TO YOUR .env ===")
    print()
    print(f"POLY_API_KEY={creds.api_key}")
    print(f"POLY_API_SECRET={creds.api_secret}")
    print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
    print()
    print("=== TEST: setting creds and checking... ===")
    client.set_api_creds(creds)
    print("Credentials set OK")
except Exception as e:
    print(f"FAILED: {e}")
    print()
    print("If geo-blocked, try removing POLY_API_KEY/SECRET/PASSPHRASE")
    print("from .env and the bot will retry derivation at startup.")
