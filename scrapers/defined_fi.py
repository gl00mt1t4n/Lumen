import cloudscraper
import json


def get_trending_tokens_from_defined(limit=20):
    url = "https://www.defined.fi/api"

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    query = """
    query FilterTokens($filters: TokenFilters, $statsType: TokenPairStatisticsType, $phrase: String, $tokens: [String], $rankings: [TokenRanking], $limit: Int, $offset: Int) {
      filterTokens(
        filters: $filters
        statsType: $statsType
        phrase: $phrase
        tokens: $tokens
        rankings: $rankings
        limit: $limit
        offset: $offset
      ) {
        results {
          token {
            name
            symbol
            address
            networkId
            socialLinks {
              twitter
              telegram
              website
            }
          }
          priceUSD
          liquidity
          marketCap
          volume24
          change24
        }
      }
    }
    """

    payload = {
        "operationName": "FilterTokens",
        "query": query,
        "variables": {
            "filters": {
                "network": [1399811149],
                "trendingIgnored": False,
                "creatorAddress": None,
                "potentialScam": False,
            },
            "statsType": "FILTERED",
            "rankings": [{"attribute": "trendingScore24", "direction": "DESC"}],
            "limit": limit,
            "offset": 0,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.defined.fi",
        "Referer": "https://www.defined.fi/",
    }

    try:
        response = scraper.post(url, data=json.dumps(payload), headers=headers)
        response.raise_for_status()
        return response.json()["data"]["filterTokens"]["results"]
    except Exception as e:
        print(f"[ERROR] Error: {e}")
        print(response.text if "response" in locals() else "")
        return []


def get_trending_token_info():  # address and symbol only
    tokens = get_trending_tokens_from_defined()
    contract_info_list = [
        {
            "address": token["token"].get("address"),
            "symbol": token["token"].get("symbol", "UNKNOWN"),
        }
        for token in tokens
        if token.get("token") and token["token"].get("address")
    ]

    print(f"Fetched {len(contract_info_list)} trending tokens with address and symbol.")
    return contract_info_list


# ─────────── Defined.fi Token Lookup ─────────────────────────────────────────
def get_token_info_from_defined(address: str, network_id: int = 1399811149) -> dict:
    """ 
    Fetch name and symbol for a single token mint from Defined.fi.
    Returns {'address': ..., 'symbol': ..., 'name': ...} or {} on failure.
    """
    url = "https://www.defined.fi/api"
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    query = """
    query FilterTokens($tokens: [String], $limit: Int) {
      filterTokens(
        filters: { network: [%d] }
        statsType: FILTERED
        tokens: $tokens
        limit: $limit
      ) {
        results {
          token {
            address
            symbol
            name
          }
        }
      }
    }
    """ % network_id

    payload = {
        "operationName": "FilterTokens",
        "query": query,
        "variables": {
            "tokens": [address],
            "limit": 1
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.defined.fi",
        "Referer": "https://www.defined.fi/",
    }

    try:
        resp = scraper.post(url, data=json.dumps(payload), headers=headers)
        resp.raise_for_status()
        results = resp.json()["data"]["filterTokens"]["results"]
        if not results:
            return {}
        tok = results[0]["token"]
        return {
            "address": tok.get("address"),
            "symbol": tok.get("symbol"),
            "name": tok.get("name"),
        }
    except Exception:
        return {}
    

if __name__ == "__main__":
    print("Top Trending Tokens on Defined.fi:\n")
    tokens = get_trending_tokens_from_defined(limit=20)

    for token in tokens:
        t = token["token"]
        name = t.get("name", "Unknown")
        symbol = t.get("symbol", "")
        address = t.get("address", "N/A")
        price = float(token.get("priceUSD", 0) or 0)
        change24 = float(token.get("change24", 0) or 0)
        volume = float(token.get("volume24", 0) or 0)
        liquidity = float(token.get("liquidity", 0) or 0)
        mcap = float(token.get("marketCap", 0) or 0)
        socials = t.get("socialLinks", {})

        print(f"{symbol} - {name}")
        print(f"  Contract: {address}")
        print(f"  Price: ${price:.6f}")
        print(f"  24h Change: {change24:.2f}%")
        print(f"  Volume 24h: ${volume:,.2f}")
        print(f"  Liquidity: ${liquidity:,.2f}")
        print(f"  Market Cap: ${mcap:,.2f}")
        print(f"  Website: {socials.get('website')}")
        print(f"  Twitter: {socials.get('twitter')}")
        print(f"  Telegram: {socials.get('telegram')}")
        print("-" * 50)
    # ────── Test our new single-token lookup ───────────────────────────────
    test_address = tokens[0]["token"]["address"] if tokens else None
    if test_address:
        info = get_token_info_from_defined(test_address)
        print(f"\nLookup for {test_address}:")
        print(f"  Symbol: {info.get('symbol', 'N/A')}")
        print(f"  Name:   {info.get('name',   'N/A')}")
