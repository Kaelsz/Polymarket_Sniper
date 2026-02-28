"""Run on VPS to diagnose API auth issues."""

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

    print(f"DRY_RUN       = {dry!r}")
    print(f"API_KEY       = {key!r}")
    print(f"API_SECRET    = {secret!r}")
    print(f"PASSPHRASE    = {passphrase!r}")
    print()
    print(f"SECRET len={len(secret)}, mod4={len(secret) % 4}")
    print(f"SECRET starts with quotes? {'YES - BAD!' if secret.startswith('\"') else 'No - OK'}")
    print(f"SECRET has spaces? {'YES - BAD!' if ' ' in secret else 'No - OK'}")
    print(f"SECRET has newline? {'YES - BAD!' if chr(10) in secret or chr(13) in secret else 'No - OK'}")

    expected_key = "019ca62c-18fb-7eef-b780-09a2afcb341e"
    expected_secret = "EWuvxJAcKqBnxkYyUE6csTyIzdwOoLeZg4KSr7-rcT0="
    expected_pass = "6ba31ba30412e58483e4d38344c522c18078458ab2df20dceafd5e337fc8030c"

    print()
    print(f"KEY match?    {'OK' if key == expected_key else 'MISMATCH! got ' + repr(key)}")
    print(f"SECRET match? {'OK' if secret == expected_secret else 'MISMATCH! got ' + repr(secret)}")
    print(f"PASS match?   {'OK' if passphrase == expected_pass else 'MISMATCH! got ' + repr(passphrase)}")

    try:
        base64.urlsafe_b64decode(secret)
        print(f"\nBase64 decode: OK")
    except Exception as e:
        print(f"\nBase64 decode: FAIL - {e}")


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
        else:
            print("    >>> Auth likely OK (error is not 401)")


if __name__ == "__main__":
    check_env()
    asyncio.run(test_api())
