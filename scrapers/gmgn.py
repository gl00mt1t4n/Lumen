import os
import json
import sys
import time
import random
import threading
import asyncio

import cloudscraper
from dotenv import load_dotenv
from typing import Dict

# ─── Environment & Scraper Setup ───────────────────────────────────────────────
load_dotenv()
scraper = cloudscraper.create_scraper()

GMGN_HEADERS = json.loads(os.getenv("GMGN_HEADERS_JSON", "{}"))
HEADERS = GMGN_HEADERS

# ─── Constants ─────────────────────────────────────────────────────────────────
TARGET_QPS      = 4.0    # start goal: 4 requests/sec
MIN_DELAY       = 0.10   # never below 100ms
MAX_DELAY       = 1.00   # never above 1s
ADJUST_FACTOR   = 1.15   # speed-up/slow-down factor
SUCCESS_WINDOW  = 50     # successes before speeding back up

REQUEST_DELAY   = 0.15   # global inter-request delay
_lock           = threading.Lock()
_next_allowed   = 0.0

MIN_THRESHOLD   = 5_000  # USD threshold for is_wallet_active


# ─── Parameter Builder ─────────────────────────────────────────────────────────
def get_base_params(**extra) -> dict:
    p = {
        "device_id":  "8847efa1-b816-4997-a22c-b88f17e9c532",
        "client_id":  "gmgn_web_20250517-1212-af09a36",
        "from_app":   "gmgn",
        "app_ver":    "20250517-1212-af09a36",
        "tz_name":    "Asia/Calcutta",
        "tz_offset":  "19800",
        "app_lang":   "en-US",
        "fp_did":     "3a81c4ac5b072160c4da0400dabab6da",
        "os":         "web",
        "period":     "7d",
        "_":          str(time.time()),
    }
    p.update(extra)
    return p


# ─── Rate Limiter ──────────────────────────────────────────────────────────────
def _wait_slot():
    global _next_allowed
    with _lock:
        now = time.time()
        if now < _next_allowed:
            time.sleep(_next_allowed - now)
        _next_allowed = time.time() + REQUEST_DELAY


# ─── Core Blocking Fetch ───────────────────────────────────────────────────────
def _sync_fetch(
    endpoint_path: str,
    wallet: str,
    params_override: dict | None = None,
    stop_processing_callback: callable = None
) -> dict:
    """
    Guaranteed-return loop until code==0. Never skips any wallet.
    """
    url    = f"https://gmgn.ai{endpoint_path.format(wallet=wallet)}"
    params = params_override or get_base_params()
    attempt = 0

    while True:
        attempt += 1
        _wait_slot()

        # Check if processing should stop
        if stop_processing_callback and stop_processing_callback():
            raise Exception("Processing stopped by user")

        try:
            resp = scraper.get(url, headers=HEADERS, params=params)

            if resp.status_code == 429:
                # Check if processing should stop before retrying
                if stop_processing_callback and stop_processing_callback():
                    raise Exception("Processing stopped by user due to rate limit")
                
                sleep_for = 3 + random.uniform(0, 2)
                print(f"[WARN] Received HTTP 429 for wallet {wallet} (attempt {attempt}). "
                      f"Retrying in {sleep_for:.1f} seconds.")
                time.sleep(sleep_for)
                continue

            resp.raise_for_status()
            payload = resp.json()

            if payload.get("code", 0) != 0:
                sleep_for = 2 + random.uniform(0, 1)
                print(f"[INFO] GMGN API returned non-zero code for wallet {wallet}. "
                      f"Retrying in {sleep_for:.1f} seconds. Response code: {payload.get('code')}")
                time.sleep(sleep_for)
                continue

            return payload.get("data", {})

        except (json.JSONDecodeError, ValueError):
            print(f"[ERROR] Failed to parse JSON response for wallet {wallet}. Retrying in 2 seconds.")
            print(f"\n[DEBUG RAW GMGN RESPONSE for wallet {wallet}]")
            print(resp.text[:500])
            print("── end debug ──\n")
            # ── original error handling ──
            print(f"[ERROR] Failed to parse JSON response for wallet {wallet}. Retrying in 2 seconds.")
            time.sleep(2)
        except Exception as e:
            print(f"[ERROR] Exception encountered while fetching data for wallet {wallet}: {e}. "
                  "Retrying in 5 seconds.")
            time.sleep(5)


# ─── Async Wrappers ────────────────────────────────────────────────────────────
async def fetch_gmgn_data(
    endpoint_path: str,
    wallet: str,
    params_override: dict | None = None,
    stop_processing_callback: callable = None
) -> dict:
    return await asyncio.to_thread(_sync_fetch, endpoint_path, wallet, params_override, stop_processing_callback)


async def get_gmgn_risk(wallet: str, stop_processing_callback: callable = None) -> dict:
    """
    Return phishing-risk ratios.
    If API gives no data → return empty dict so caller treats wallet as unsafe.
    """
    data = await fetch_gmgn_data("/api/v1/wallet_stat/sol/{wallet}/7d", wallet, stop_processing_callback=stop_processing_callback)

    if not data:
        return {}
    risk = data.get("risk") or {}
    return {
        "didnt_buy_ratio":         float(risk.get("no_buy_hold_ratio", 0.0)),
        "buy_sell_under_5s_ratio": float(risk.get("fast_tx_ratio", 0.0)),
        "sold_gt_bought_ratio":    float(risk.get("sell_pass_buy_ratio", 0.0)),
    }


async def is_wallet_safe(wallet: str, stop_processing_callback: callable = None) -> bool:
    """
    Returns true if it passes phishing checks.
    didnt_buy_ratio < 60%
    buy_sell_under_5s_ratio < 40%
    sold_gt_bought_ratio < 40%
    """
    risk = await get_gmgn_risk(wallet, stop_processing_callback)
    if not risk:
        print(f"[INFO] No risk data returned for wallet: {wallet}")
        return False

    if (
        risk["didnt_buy_ratio"]         < 0.6 and
        risk["buy_sell_under_5s_ratio"] < 0.40 and
        risk["sold_gt_bought_ratio"]    < 0.10
    ):
        return True

    print(f"[WARN] Wallet {wallet} did not pass phishing risk checks. Risk metrics: {risk}")
    return False


async def get_wallet_holdings(
    wallet: str,
    limit: int = 50,
    orderby: str = "total_profit",
    direction: str = "desc",
    stop_processing_callback: callable = None
) -> list[dict]:
    """
    Returns the 'holdings' array already sorted by GMGN backend.
    """
    params = get_base_params(
        limit=str(limit),
        orderby=orderby,
        direction=direction,
        showsmall="true",
        sellout="true",
        tx30d="true",
    )
    data = await fetch_gmgn_data(
        "/api/v1/wallet_holdings/sol/{wallet}", wallet, params_override=params, stop_processing_callback=stop_processing_callback
    )
    return data.get("holdings", [])


# ─── Adaptive global rate-limiter ────────────────────────────────────────────
TARGET_QPS = 4.0  # start goal: 4 requests / second  (0.25 s delay)
MIN_DELAY = 0.10  # never below 100 ms
MAX_DELAY = 1.00  # never above 1 s   (can still rise via back-off)
ADJUST_FACTOR = 1.15  # how aggressively to slow/speed
SUCCESS_WINDOW = 50  # successes needed before speeding back up

_lock = threading.Lock()
_next_time = 0.0
_delay = 1.0 / TARGET_QPS
_success = 0  # rolling counter

REQUEST_DELAY = 0.2
_lock = threading.Lock()
_next_allowed = 0.0


def _wait_slot():
    global _next_allowed
    with _lock:
        now = time.time()
        if now < _next_allowed:
            time.sleep(_next_allowed - now)
        _next_allowed = time.time() + REQUEST_DELAY


# ──────────────────────────────────────────────────────────────────────────────

from typing import Dict

# Threshold (USD) for “active” wallets
MIN_THRESHOLD = 5_000

def is_wallet_active(stats: Dict) -> bool:
    """
    Returns True if:
      • realizedPnlUsd > 0
      • AND all of (realizedPnlUsd, unrealizedPnlUsd,
        totalRevenueUsd, totalSpentUsd) are >= MIN_THRESHOLD.
    """
    if not stats:
        return False

    realized    = stats.get("realizedPnlUsd", 0.0)
    unrealized  = stats.get("unrealizedPnlUsd", 0.0)
    total_rev   = stats.get("totalRevenueUsd", 0.0)
    total_spent = stats.get("totalSpentUsd", 0.0)

    if realized <= 0:
        return False

    return (
        abs(realized)    >= MIN_THRESHOLD
        and abs(unrealized) >= MIN_THRESHOLD
        and total_rev    >= MIN_THRESHOLD
        and total_spent  >= MIN_THRESHOLD
    )
async def get_gmgn_big_wins(
    wallet: str,
    min_profit_usd: float = 5_000,
    min_roi: float     = 0.69,
    top_n: int         = 3
) -> dict:
    """
    Uses server-sorted /wallet_holdings so we only inspect the first `limit`.
    """
    holdings = await get_wallet_holdings(wallet, limit=50)

    winners = [
        {
            "symbol":     h["token"]["symbol"],
            "profit_usd": float(h["total_profit"]),
            "roi":        float(h["total_profit_pnl"]),
        }
        for h in holdings
        if float(h.get("total_profit", 0))     >= min_profit_usd
        and float(h.get("total_profit_pnl", 0)) >= min_roi
    ]

    return {"has_big_wins": len(winners) >= top_n, "winners": winners[:top_n]}


# ─── Active-Wallet Filter ─────────────────────────────────────────────────────
def is_wallet_active(stats: Dict) -> bool:
    """
    Return True iff:
      • realizedPnlUsd > 0
      • AND abs(realizedPnlUsd), abs(unrealizedPnlUsd),
        totalRevenueUsd, totalSpentUsd are all >= MIN_THRESHOLD.
    """
    if not stats:
        return False

    realized    = stats.get("realizedPnlUsd", 0.0)
    unrealized  = stats.get("unrealizedPnlUsd", 0.0)
    total_rev   = stats.get("totalRevenueUsd", 0.0)
    total_spent = stats.get("totalSpentUsd", 0.0)

    if realized <= 0:
        return False

    return (
        abs(realized)     >= MIN_THRESHOLD and
        abs(unrealized)   >= MIN_THRESHOLD and
        total_rev         >= MIN_THRESHOLD and
        total_spent       >= MIN_THRESHOLD
    )


# ─── Paste this async function somewhere near the top ─────────────────────────

async def evaluate_trader(
    wallet: str,
    period: str     = "7d",
    min_pnl30: float= 0.75,
    min_roi: float  = 0.30,
    top_n: int      = 3,
    stop_processing_callback: callable = None
) -> tuple[str, dict]:
    """
    Fetch summary + holdings, build stats, then run:
      1. no 'sandwich_bot' tag
      2. pnl_30d > min_pnl30
      3. ≥1 of top_n holdings ROI >= min_roi

    Returns (reason_code, stats_dict).
    """
    # 1) fetch summary
    data = await fetch_gmgn_data(f"/api/v1/wallet_stat/sol/{{wallet}}/{period}", wallet, stop_processing_callback=stop_processing_callback)
    if not data:
        return "JSON_FAIL", {}

    # 2) fetch holdings for ROI
    holdings = await get_wallet_holdings(wallet, limit=top_n, stop_processing_callback=stop_processing_callback)
    top_hold = holdings[:top_n]

    # 3) build a comprehensive stats dict
    stats = {
        "tags":            data.get("tags", []),
        "winrate":         data.get("winrate", 0.0),
        "pnl_usd_7d":      data.get("realized_profit_7d", 0.0),
        "pnl_usd_30d":     data.get("realized_profit_30d", 0.0),
        "pnl_pct_7d":      data.get("pnl_7d", 0.0),
        "pnl_pct_30d":     data.get("pnl_30d", 0.0),
        "tx_7d":           data.get("buy_7d", 0)  + data.get("sell_7d", 0),
        "tx_30d":          data.get("buy_30d", 0) + data.get("sell_30d", 0),
        "top_holdings": [
            {
                "symbol": h.get("token", {}).get("symbol", ""),
                "roi":    float(h.get("total_profit_pnl", 0.0))
            }
            for h in top_hold
        ]
    }

    # 4) apply filters
    if "sandwich_bot" in stats["tags"]:
        return "TAG_sandwich_bot", stats
    if stats["pnl_pct_30d"] <= min_pnl30:
        return "PNL30_LOW", stats
    if all(h["roi"] < min_roi for h in stats["top_holdings"]):
        return "ROI_LOW", stats

    # 5) passed all
    return "PASS", stats

# ─── replace your existing __main__ with this ────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python gmgn.py <wallet_address>")
        sys.exit(1)

    wallet = sys.argv[1]
    reason, stats = asyncio.run(evaluate_trader(wallet))

    print(f"\nWallet: {wallet}")
    print(f"Result: {reason}")
    print("Stats:")
    print(json.dumps(stats, indent=2))