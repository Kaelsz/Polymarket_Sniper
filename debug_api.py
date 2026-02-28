"""Run on VPS to diagnose API auth and balance issues."""

import asyncio
import os
import base64

from dotenv import load_dotenv
load_dotenv(override=True)


def check_env():
    print("=== ENV DIAGNOSTIC ===\n")

    key = os.getenv("POLY_API_KEY", "")
    secret = os.getenv("POLY_API_SECRET", "")
    passphrase = os.getenv("POLY_API_PASSPHRASE", "")
    dry = os.getenv("DRY_RUN", "NOT SET")
    sig_type = os.getenv("POLY_SIGNATURE_TYPE", "0")
    funder = os.getenv("POLY_FUNDER", "NOT SET")
    address = os.getenv("POLYMARKET_ADDRESS", "NOT SET")

    print(f"DRY_RUN            = {dry!r}")
    print(f"POLYMARKET_ADDRESS = {address!r}")
    print(f"POLY_FUNDER        = {funder!r}")
    print(f"POLY_SIGNATURE_TYPE= {sig_type!r}")
    print(f"API_KEY            = {key!r}")
    print(f"API_SECRET         = {secret!r}")
    print(f"PASSPHRASE         = {passphrase!r}")
    print()
    print(f"SECRET len={len(secret)}, mod4={len(secret) % 4}")

    try:
        base64.urlsafe_b64decode(secret)
        print(f"Base64 decode: OK")
    except Exception as e:
        print(f"Base64 decode: FAIL - {e}")


async def test_api():
    from core.polymarket import polymarket

    print("\n=== API TEST ===\n")

    print("[1] Init client...")
    try:
        await polymarket.init()
        print("    OK")
    except Exception as e:
        print(f"    FAIL: {e}")
        return

    print("[2] Fetch order book...")
    token = "42627656795705475111093487500096393802963859670229064198944415588601301329234"
    try:
        book = await polymarket.get_order_book(token)
        asks = getattr(book, "asks", None) or (book.get("asks", []) if isinstance(book, dict) else [])
        print(f"    OK - {len(asks)} asks")
    except Exception as e:
        print(f"    FAIL: {e}")
        return

    print("[3] Test order (size=$1, price=$0.01 - will not fill)...")
    try:
        from py_clob_client.clob_types import OrderArgs
        loop = asyncio.get_running_loop()
        args = OrderArgs(token_id=token, size=1.0, price=0.01, side="BUY")
        result = await loop.run_in_executor(
            None, lambda: polymarket.client.create_and_post_order(args)
        )
        print(f"    OK - {result}")
    except Exception as e:
        print(f"    RESULT: {e}")
        if "401" in str(e):
            print("    >>> AUTH FAILED - credentials mismatch")
        elif "403" in str(e):
            print("    >>> AUTH OK but geo-blocked (expected on some servers)")
        elif "balance" in str(e).lower() or "allowance" in str(e).lower():
            print("    >>> Auth OK, but balance/allowance issue")
            print("    >>> Try changing POLY_SIGNATURE_TYPE and POLY_FUNDER in .env")
        else:
            print("    >>> Auth likely OK (error is not 401)")


if __name__ == "__main__":
    check_env()
    asyncio.run(test_api())
