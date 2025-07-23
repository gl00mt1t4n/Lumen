#!/usr/bin/env python3
"""
token-manager.py ────────────────────────────────────────────────────────────
Utility script to manage tokens and view database results.
"""

import sqlite3
import json
from typing import List, Tuple
from pathlib import Path
import sys
import os
import requests

# ─── Ensure paths are correct ────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
DB_PATH = SCRIPT_DIR / "processed_traders.db"
TOKENS_FILE = SCRIPT_DIR / "tokens.txt"


def fetch_token_name(token_address: str) -> str:
    """Fetch the human-readable token name from Dexscreener API."""
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=10
        )
        pairs = resp.json().get("pairs", [])
        if pairs and "baseToken" in pairs[0]:
            return pairs[0]["baseToken"].get("name", "UNKNOWN")
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"

def add_token(token_address: str, token_name: str = None):
    """Add a new token to the tokens.txt file. If name is not provided, fetch from Dexscreener."""
    if not token_name:
        print(f"Fetching token name for {token_address}...")
        token_name = fetch_token_name(token_address)
        print(f"  → Got name: {token_name}")
    with open(TOKENS_FILE, 'a') as f:
        f.write(f"{token_address},{token_name}\n")
    print(f"Added token: {token_name} ({token_address})")

def list_tokens():
    """List all tokens in the tokens.txt file."""
    if not TOKENS_FILE.exists():
        print("No tokens.txt file found!")
        return
    
    print("Tokens in tokens.txt:")
    print("-" * 60)
    with open(TOKENS_FILE, 'r') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if line and not line.startswith('#'):
                parts = line.split(',')
                if len(parts) >= 2:
                    addr, name = parts[0].strip(), parts[1].strip()
                    print(f"{i:2d}. {name:20} {addr}")
                else:
                    print(f"{i:2d}. {line}")

def view_database_stats():
    """View comprehensive database statistics."""
    if not DB_PATH.exists():
        print("No database found!")
        return
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Overall stats
        cursor.execute("SELECT COUNT(*) FROM processed_tokens")
        processed_tokens = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM traders")
        total_traders = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM traders WHERE evaluation_result = 'PASS'")
        passed_traders = cursor.fetchone()[0]
        
        print("DATABASE STATISTICS")
        print("=" * 60)
        print(f"Processed tokens: {processed_tokens}")
        print(f"Total traders evaluated: {total_traders}")
        print(f"Passed traders: {passed_traders}")
        print(f"Pass rate: {passed_traders/total_traders*100:.1f}%" if total_traders > 0 else "Pass rate: N/A")
        
        # Token breakdown
        print("\nTOKEN BREAKDOWN")
        print("-" * 60)
        cursor.execute("""
            SELECT token_name, total_holders, passed_traders, processed_at
            FROM processed_tokens
            ORDER BY processed_at DESC
        """)
        
        for name, holders, passed, timestamp in cursor.fetchall():
            rate = passed/holders*100 if holders > 0 else 0
            print(f"{name:15} | {holders:4d} holders | {passed:3d} passed | {rate:5.1f}% | {timestamp}")

def view_passed_traders(limit: int = 20):
    """View traders that passed the filters."""
    if not DB_PATH.exists():
        print("No database found!")
        return
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT t.wallet_address, pt.token_name, t.pnl_pct_30d, t.winrate, 
                   t.realized_profit, t.realized_profit_ratio, t.top_holdings
            FROM traders t
            JOIN processed_tokens pt ON t.token_address = pt.token_address
            WHERE t.evaluation_result = 'PASS'
            ORDER BY t.pnl_pct_30d DESC
            LIMIT ?
        """, (limit,))
        
        results = cursor.fetchall()
        
        print(f"TOP {len(results)} PASSED TRADERS")
        print("=" * 100)
        print(f"{'Wallet':<44} {'Token':<12} {'30d PnL%':<10} {'Winrate':<10} {'Realized $':<12} {'Ratio':<8} {'Top Holdings'}")
        print("-" * 100)
        
        for wallet, token, pnl, winrate, realized_profit, realized_ratio, holdings_json in results:
            holdings = json.loads(holdings_json) if holdings_json else []
            top_holdings = ", ".join([h.get('symbol', 'N/A') for h in holdings[:3]])
            print(f"{wallet:<44} {token:<12} {pnl:>8.1f}% {winrate:>8.1f}% {realized_profit:>10.0f} {realized_ratio:>6} {top_holdings}")

def view_failed_reasons():
    """View breakdown of why traders failed."""
    if not DB_PATH.exists():
        print("No database found!")
        return
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT evaluation_result, COUNT(*) as count
            FROM traders
            WHERE evaluation_result != 'PASS'
            GROUP BY evaluation_result
            ORDER BY count DESC
        """)
        
        results = cursor.fetchall()
        
        print("FAILED TRADERS BREAKDOWN")
        print("=" * 40)
        for reason, count in results:
            print(f"{reason:<20} {count:>5}")

def main():
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python token-manager.py list                    - List all tokens")
        print("  python token-manager.py add <addr> [name]       - Add new token (name optional)")
        print("  python token-manager.py stats                   - View database stats")
        print("  python token-manager.py passed [limit]          - View passed traders")
        print("  python token-manager.py failed                  - View failure reasons")
        return
    
    command = sys.argv[1]
    
    if command == "list":
        list_tokens()
    elif command == "add":
        if len(sys.argv) == 3:
            add_token(sys.argv[2])
        elif len(sys.argv) >= 4:
            add_token(sys.argv[2], sys.argv[3])
        else:
            print("Usage: python token-manager.py add <token_address> [token_name]")
            return
    elif command == "stats":
        view_database_stats()
    elif command == "passed":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        view_passed_traders(limit)
    elif command == "failed":
        view_failed_reasons()
    else:
        print(f"Unknown command: {command}")

if __name__ == "__main__":
    main() 