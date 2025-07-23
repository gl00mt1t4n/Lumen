import os
import requests
import time
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


def get_token_accounts_rpc(
    mint_address: str,
    api_key: Optional[str] = None,
    limit: int = 1000,
    delay: float = 0.2,
) -> List[Dict]:
    """
    Fetches all token holders (token accounts) from Helius RPC using pagination.
    Returns:
        List[Dict]: List of token account dicts (owner, amount, etc).
    """
    api_key = api_key or HELIUS_API_KEY
    if not api_key:
        raise ValueError(
            "Missing Helius API key. Set it in .env or pass it explicitly."
        )

    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    headers = {"Content-Type": "application/json"}

    holders = []
    cursor = None
    page = 1

    while True:
        payload = {
            "jsonrpc": "2.0",
            "id": str(page),
            "method": "getTokenAccounts",
            "params": {
                "mint": mint_address,
                "limit": limit,
                "options": {"showZeroBalance": False},
            },
        }

        if cursor:
            payload["params"]["cursor"] = cursor

        try:
            res = requests.post(url, json=payload, headers=headers)
            res.raise_for_status()
            result = res.json()["result"]

            accounts = result.get("token_accounts", [])
            holders.extend(accounts)
            print(f"→ Page {page}: {len(accounts)} accounts")

            if not accounts or "cursor" not in result:
                break

            cursor = result["cursor"]
            page += 1
            time.sleep(delay)
        except Exception as e:
            print(f"Error on page {page}: {e}")
            break

    print(f"\nTotal holders for {mint_address}: {len(holders)}")
    return holders


if __name__ == "__main__":
    token = "J27UYHX5oeaG1YbUGQc8BmJySXDjNWChdGB2Pi2TMDAq"
    holders = get_token_accounts_rpc(token)

    for h in holders[:5]:
        print(f"{h['owner']} — {h['amount']}")
        # Print top owners for testing sake
