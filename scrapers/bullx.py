import cloudscraper
import json
import requests
import time
import httpx
from typing import Dict, List, Optional, Union
import os
from dotenv import load_dotenv
import asyncio

load_dotenv()

BULLX_HEADERS = json.loads(os.getenv("BULLX_HEADERS_JSON", "{}"))
BULLX_COOKIES = json.loads(os.getenv("BULLX_COOKIES_JSON", "{}"))
HEADERS = BULLX_HEADERS
COOKIES = BULLX_COOKIES


async def fetch_pnl_stats(wallet: str) -> Optional[Dict]:
    """
    Fetch pnlStats for one wallet. Returns the stats dict on success, or None on any error.
    """
    url = "https://api-neo.bullx.io/v2/api/getPortfolioV3"
    payload = {
        "name": "getPortfolioV3",
        "data": {
            "walletAddresses": [wallet],
            "chainIds": [1399811149, 728126428],
            "fetchMostProfitablePositions": True,
            "mostProfitablePositionsFilters": {
                "chainIds": [1399811149, 728126428],
                "walletAddresses": [wallet],
            },
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                url, headers=HEADERS, cookies=COOKIES, json=payload
            )
            resp.raise_for_status()
            return resp.json().get("pnlStats", {})
        except Exception:
            return None

from typing import List, Any

async def fetch_top500_traders(token_address: str) -> List[Dict]:
    """
    Fetch the top-500 traders for a token via holdersSummaryV2.
    Returns a list of trader dicts with their trading data.
    """
    url = "https://api-neo.bullx.io/v2/api/holdersSummaryV2"
    payload = {
        "name": "holdersSummaryV2",
        "data": {
            "tokenAddress": token_address,
            "sortBy": "pnlUSD",
            "chainId": 1399811149,
            "filters": {"tagsFilters": []},
        }
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=HEADERS, cookies=COOKIES, json=payload)
        resp.raise_for_status()
        data: Any = resp.json()

        # handle case where API returns a bare list
        if isinstance(data, list):
            holders = data
        else:
            holders = data.get("data", {}).get("holders", [])

        # Return full holder objects with their data (treating them as traders)
        return [h for h in holders if isinstance(h, dict) and h.get("address")]



if __name__ == "__main__":
    import json
    import asyncio

    # Existing single-wallet PnL test
    test_wallet = "H1UsuH1T32cKbdWpnkuYg5DCFfSgxDj4WMLD9jAZPJuB"
    pnl = asyncio.run(fetch_pnl_stats(test_wallet))
    print("PNL stats:", json.dumps(pnl, indent=2))

    # ─── New top-500 holders test ──────────────────────────────────────────
    test_token = "JB2wezZLdzWfnaCfHxLg193RS3Rh51ThiXxEDWQDpump"
    traders = asyncio.run(fetch_top500_traders(test_token))
    print(f"\nFetched {len(traders)} top traders for token {test_token}:")
    # print first 20 to avoid flooding your console
    for addr in traders[:20]:
        print(" ", addr)


