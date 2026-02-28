"""Diagnostic: what markets pass the full filter pipeline?"""
import asyncio
import json
from datetime import datetime, timezone
import aiohttp


def parse_end(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


async def check():
    async with aiohttp.ClientSession() as s:
        all_markets = []
        offset = 0
        while True:
            url = f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset={offset}"
            async with s.get(url) as r:
                batch = await r.json()
                if not batch:
                    break
                all_markets.extend(batch)
                if len(batch) < 100:
                    break
                offset += 100

        now = datetime.now(timezone.utc)
        max_end_days = 7
        max_end_sec = max_end_days * 86400

        print(f"Total active markets: {len(all_markets)}")
        print(f"Now: {now.isoformat()}")
        print(f"Filter: volume >= 100K, end date <= {max_end_days} days, price [0.95-0.99]")
        print()

        # Step 1: volume filter
        vol_pass = [m for m in all_markets if float(m.get("volume", 0) or 0) >= 100_000]
        print(f"After volume filter (>= 100K): {len(vol_pass)}")

        # Step 2: end date filter
        date_pass = []
        no_date = 0
        too_far = 0
        expired = 0
        for m in vol_pass:
            end_raw = m.get("endDate", "") or m.get("end_date_iso", "") or ""
            end_dt = parse_end(end_raw)
            if end_dt is None:
                no_date += 1
                continue
            time_left = (end_dt - now).total_seconds()
            if time_left <= 0:
                expired += 1
                continue
            if time_left > max_end_sec:
                too_far += 1
                continue
            date_pass.append(m)

        print(f"After end-date filter (<= {max_end_days} days): {len(date_pass)}")
        print(f"  (no end date: {no_date}, expired: {expired}, too far: {too_far})")
        print()

        # Step 3: price filter
        candidates = []
        for m in date_pass:
            vol = float(m.get("volume", 0) or 0)
            prices_raw = m.get("outcomePrices", "")
            tokens_raw = m.get("clobTokenIds", "")
            outcomes_raw = m.get("outcomes", "")
            if not prices_raw or not tokens_raw:
                continue
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or ["Yes", "No"])
            except Exception:
                continue

            end_raw = m.get("endDate", "") or ""
            end_dt = parse_end(end_raw)
            days_left = (end_dt - now).total_seconds() / 86400 if end_dt else 0

            for ps, tid, out in zip(prices, tokens, outcomes):
                pf = float(ps)
                if 0.95 <= pf <= 0.99:
                    q = m.get("question", "?")
                    candidates.append((pf, vol, out, q, days_left))

        print(f"=== FINAL CANDIDATES (all filters) ===")
        for pf, vol, out, q, days in candidates:
            safe_q = q[:75].encode("ascii", errors="replace").decode()
            print(f"  {out:5s} @ {pf:.4f} | vol={vol/1000:.0f}K | ends in {days:.1f}d | {safe_q}")

        print(f"\nTotal candidates: {len(candidates)}")

        # Also show what's ending soon regardless of price
        print(f"\n=== ALL MARKETS ENDING <= 7 DAYS (vol >= 100K) ===")
        for m in date_pass[:30]:
            vol = float(m.get("volume", 0) or 0)
            prices_raw = m.get("outcomePrices", "")
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            except Exception:
                prices = []
            end_raw = m.get("endDate", "") or ""
            end_dt = parse_end(end_raw)
            days_left = (end_dt - now).total_seconds() / 86400 if end_dt else 0
            q = m.get("question", "?")[:65].encode("ascii", errors="replace").decode()
            print(f"  prices={[f'{float(p):.3f}' for p in prices]} | vol={vol/1000:.0f}K | ends {days_left:.1f}d | {q}")


asyncio.run(check())
