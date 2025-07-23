#!/usr/bin/env python3
"""
multi-coin-processor.py ─────────────────────────────────────────────────────
Process multiple coins through GMGN filters and store results in SQLite.

Features:
  • Reads from a configurable list of tokens (tokens.txt)
  • Tracks which coins have been processed to avoid duplicates
  • Implements GMGN filters (sandwich_bot filter)
  • Stores results in SQLite database
  • Modular design for easy token addition
"""

import json
import logging
import asyncio
import sqlite3
import httpx
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv
import sys
import os

# ─── Ensure scrapers can be imported ─────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
PARENT_DIR = SCRIPT_DIR.resolve()  # Now we're in root, so same as SCRIPT_DIR
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

# ─── Absolute imports of our helper functions ────────────────────────────────
import importlib.util

# Import bullx scraper
bullx_spec = importlib.util.spec_from_file_location("bullx", SCRIPT_DIR / "scrapers" / "bullx.py")
bullx = importlib.util.module_from_spec(bullx_spec)
bullx_spec.loader.exec_module(bullx)
fetch_top500_traders = bullx.fetch_top500_traders
fetch_pnl_stats = bullx.fetch_pnl_stats

# Import gmgn scraper
gmgn_spec = importlib.util.spec_from_file_location("gmgn", SCRIPT_DIR / "scrapers" / "gmgn.py")
gmgn = importlib.util.module_from_spec(gmgn_spec)
gmgn_spec.loader.exec_module(gmgn)
evaluate_trader = gmgn.evaluate_trader

# ─── Configuration ────────────────────────────────────────────────────────────
load_dotenv(dotenv_path=SCRIPT_DIR / '.env')

# Database and file paths (all local to omni-renewed)
DB_PATH = SCRIPT_DIR / "processed_traders.db"
TOKENS_FILE = SCRIPT_DIR / "tokens.txt"
PROCESSED_TOKENS_FILE = SCRIPT_DIR / "processed_tokens.txt"

# Concurrency limit (default 15, can override with env var)
CONCURRENCY_LIMIT = int(os.getenv("OMNI_CONCURRENCY", 7))

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)

class TokenProcessor:
    def __init__(self):
        self.db_path = str(DB_PATH)
        self.tokens_file = str(TOKENS_FILE)
        self.concurrency = CONCURRENCY_LIMIT
        self.stop_processing = False  # Global stop flag
        self.current_token_name = None  # Track current token being processed
        self.token_callback = None  # Callback to update current token
        self.init_database()
        self._logging_handler_attached = False  # Prevent duplicate log handlers
    
    def init_database(self):
        """Initialize SQLite database with required tables. Never drop or reset tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Only create tables if they do not exist. Never drop or reset.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_tokens (
                    token_address TEXT PRIMARY KEY,
                    token_name TEXT,
                    token_symbol TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    total_holders INTEGER,
                    passed_traders INTEGER
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS traders (
                    wallet_address TEXT,
                    token_address TEXT,
                    token_name TEXT,
                    token_symbol TEXT,
                    total_bought_usd REAL,
                    total_sold_usd REAL,
                    realized_profit_usd REAL,
                    realized_profit_ratio TEXT,
                    currently_holding_amount REAL,
                    total_buy_transactions INTEGER,
                    total_sell_transactions INTEGER,
                    evaluation_result TEXT,
                    tags TEXT,
                    winrate REAL,
                    pnl_usd_7d REAL,
                    pnl_usd_30d REAL,
                    pnl_pct_7d REAL,
                    pnl_pct_30d REAL,
                    tx_7d INTEGER,
                    tx_30d INTEGER,
                    top_holdings TEXT,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (wallet_address, token_address)
                )
            """)
            conn.commit()
    
    def migrate_old_schema(self, conn):
        """Migrate data from old schema to new schema."""
        cursor = conn.cursor()
        
        # Create new table with new schema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS traders_new (
                wallet_address TEXT,
                token_address TEXT,
                token_name TEXT,
                token_symbol TEXT,
                -- Custom BullX fields (from holdersSummaryV2)
                total_bought_usd REAL,
                total_sold_usd REAL,
                realized_profit_usd REAL,
                realized_profit_ratio TEXT,
                currently_holding_amount REAL,
                total_buy_transactions INTEGER,
                total_sell_transactions INTEGER,
                -- GMGN fields
                evaluation_result TEXT,
                tags TEXT,
                winrate REAL,
                pnl_usd_7d REAL,
                pnl_usd_30d REAL,
                pnl_pct_7d REAL,
                pnl_pct_30d REAL,
                tx_7d INTEGER,
                tx_30d INTEGER,
                top_holdings TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (wallet_address, token_address)
            )
        """)
        
        # Migrate existing data
        cursor.execute("""
            INSERT INTO traders_new (
                wallet_address, token_address, evaluation_result, tags,
                winrate, pnl_usd_7d, pnl_usd_30d, pnl_pct_7d, pnl_pct_30d,
                tx_7d, tx_30d, top_holdings, processed_at
            )
            SELECT 
                wallet_address, token_address, evaluation_result, tags,
                winrate, pnl_usd_7d, pnl_usd_30d, pnl_pct_7d, pnl_pct_30d,
                tx_7d, tx_30d, top_holdings, processed_at
            FROM traders
        """)
        
        # Drop old table and rename new one
        cursor.execute("DROP TABLE traders")
        cursor.execute("ALTER TABLE traders_new RENAME TO traders")
        
        logging.info("Database migration completed successfully!")
    
    def load_tokens(self) -> List[Tuple[str, str]]:
        """Load tokens from tokens.txt file."""
        tokens = []
        if Path(self.tokens_file).exists():
            with open(self.tokens_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split(',')
                        if len(parts) >= 2:
                            token_address = parts[0].strip()
                            token_name = parts[1].strip()
                            tokens.append((token_address, token_name))
        return tokens
    
    def get_processed_token_addresses(self):
        """Return a set of all processed token addresses from the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT token_address FROM processed_tokens")
            return {row[0] for row in cursor.fetchall()}
    
    def mark_token_processed(self, token_address: str, token_name: str, token_symbol: str,
                           total_holders: int, passed_traders: int):
        """Mark a token as processed and save to database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO processed_tokens 
                (token_address, token_name, token_symbol, total_holders, passed_traders)
                VALUES (?, ?, ?, ?, ?)
            """, (token_address, token_name, token_symbol, total_holders, passed_traders))
            conn.commit()
    
    def stop(self):
        """Stop the processing."""
        self.stop_processing = True
    
    def set_token_callback(self, callback):
        """Set callback function to update current token."""
        self.token_callback = callback
    
    def set_progress_callback(self, callback):
        """Set callback function to update progress."""
        self.progress_callback = callback
    
    def set_fatal_callback(self, callback):
        """Set callback function to notify fatal errors."""
        self.fatal_callback = callback
    
    def save_trader_data(self, wallet_address: str, token_address: str, token_name: str, token_symbol: str,
                        evaluation_result: str, stats: Dict):
        """Save trader data to database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Prepare tags as JSON
            tags = stats.get('tags', [])
            tags_json = json.dumps(tags) if tags else None
            
            cursor.execute("""
                INSERT OR REPLACE INTO traders (
                    wallet_address, token_address, token_name, token_symbol,
                    total_bought_usd, total_sold_usd, realized_profit_usd, realized_profit_ratio,
                    currently_holding_amount, total_buy_transactions, total_sell_transactions,
                    evaluation_result, tags, winrate, pnl_usd_7d, pnl_usd_30d,
                    pnl_pct_7d, pnl_pct_30d, tx_7d, tx_30d, top_holdings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                wallet_address, token_address, token_name, token_symbol,
                stats.get('total_bought_usd', 0.0),
                stats.get('total_sold_usd', 0.0),
                stats.get('realized_profit_usd', 0.0),
                stats.get('realized_profit_ratio', '0.0x'),
                stats.get('currently_holding_amount', 0.0),
                stats.get('total_buy_transactions', 0),
                stats.get('total_sell_transactions', 0),
                evaluation_result,
                tags_json,
                stats.get('winrate', 0.0),
                stats.get('pnl_usd_7d', 0.0),
                stats.get('pnl_usd_30d', 0.0),
                stats.get('pnl_pct_7d', 0.0),
                stats.get('pnl_pct_30d', 0.0),
                stats.get('tx_7d', 0),
                stats.get('tx_30d', 0),
                json.dumps(stats.get('top_holdings', []))
            ))
            conn.commit()
    
    def attach_logging_handler_once(self, handler):
        """Attach logging handler only once per process."""
        if not self._logging_handler_attached:
            logger = logging.getLogger()
            logger.handlers = [h for h in logger.handlers if not isinstance(h, type(handler))]
            logger.addHandler(handler)
            self._logging_handler_attached = True
    
    async def fetch_traders_data(self, token_address: str) -> list:
        """Fetch top 500 traders from BullX."""
        try:
            traders = await fetch_top500_traders(token_address)
            if not isinstance(traders, list):
                logging.error(f"BullX API returned non-list for traders: {traders}")
                return []
            seen_addresses = set()
            unique_traders = []
            for trader in traders:
                if self.stop_processing:
                    logging.warning("Processing stopped by user during fetch_traders_data.")
                    return []
                if isinstance(trader, dict):
                    addr = trader.get('address')
                    if addr and addr not in seen_addresses:
                        seen_addresses.add(addr)
                        unique_traders.append(trader)
                else:
                    logging.warning(f"BullX API returned unexpected trader type: {type(trader)} - {trader}")
            if not unique_traders:
                logging.warning(f"No valid traders found for token {token_address}")
                return []
            logging.info(f"Processing {len(unique_traders)} unique traders for token {token_address}")
            return unique_traders
        except Exception as e:
            # If it's an HTTP 429 or connection error, stop processing
            if hasattr(e, 'response') and getattr(e.response, 'status_code', None) == 429:
                logging.error(f"API Error 429: Rate limit hit. Stopping processor.")
                self.stop_processing = True
                if hasattr(self, 'fatal_callback') and self.fatal_callback:
                    self.fatal_callback("API Error 429: Rate limit hit. Stopping processor.")
            elif '429' in str(e) or 'rate limit' in str(e).lower():
                logging.error(f"API Error: {e}. Stopping processor.")
                self.stop_processing = True
                if hasattr(self, 'fatal_callback') and self.fatal_callback:
                    self.fatal_callback(f"API Error: {e}. Stopping processor.")
            else:
                logging.error(f"Failed to fetch traders data: {e}")
            return []

    async def process_single_token(self, token_address: str, token_name: str) -> dict:
        self.current_token_name = token_name
        if self.token_callback:
            self.token_callback(token_name)
        # Extract token symbol more robustly
        if token_name == 'UNKNOWN':
            token_symbol = 'UNKNOWN'
        elif '(' in token_name and ')' in token_name:
            symbol_match = token_name.split('(')[-1].rstrip(')')
            token_symbol = symbol_match if symbol_match else token_name.split()[0]
        else:
            token_symbol = token_name.split()[0]
        logging.info(f"Starting processing for token: {token_name}")
        traders_data = await self.fetch_traders_data(token_address)
        if not traders_data:
            logging.warning(f"No traders data found for {token_name}")
            self.mark_token_processed(token_address, token_name, token_symbol, 0, 0)
            logging.info(f"Completed processing {token_name}: 0/0 passed")
            return {
                "token_address": token_address,
                "token_name": token_name,
                "total_traders": 0,
                "passed_traders": 0
            }
        logging.info(f"Fetched {len(traders_data)} unique traders for token {token_name}")
        passed_traders = 0
        sem = asyncio.Semaphore(self.concurrency)
        async def evaluate_and_save(trader_data: dict):
            nonlocal passed_traders
            wallet = trader_data['address']
            async with sem:
                try:
                    if self.stop_processing:
                        return
                    reason, gmgn_stats = await evaluate_trader(wallet, stop_processing_callback=lambda: self.stop_processing)
                    logging.info(f"Trader {wallet[:8]}...{wallet[-8:]} → {reason}")
                    # Use BullX trader data
                    bullx_data = trader_data
                    bought_usd = bullx_data.get('totalBoughtUSD', 0.0)
                    sold_usd = bullx_data.get('totalSoldUSD', 0.0)
                    realized_profit = sold_usd - bought_usd
                    realized_ratio = f"{sold_usd / bought_usd:.2f}x" if bought_usd > 0 else "0.0x"
                    combined_stats = gmgn_stats.copy()
                    combined_stats.update({
                        'total_bought_usd': bought_usd,
                        'total_sold_usd': sold_usd,
                        'realized_profit_usd': realized_profit,
                        'realized_profit_ratio': realized_ratio,
                        'currently_holding_amount': bullx_data.get('currentlyHoldingAmount', 0.0),
                        'total_buy_transactions': bullx_data.get('totalBuyTransactions', 0),
                        'total_sell_transactions': bullx_data.get('totalSellTransactions', 0)
                    })
                    self.save_trader_data(wallet, token_address, token_name, token_symbol, reason, combined_stats)
                    if reason == "PASS":
                        passed_traders += 1
                        logging.info(f"✓ Saved PASSED trader {wallet[:8]}...{wallet[-8:]} to database")
                    else:
                        logging.debug(f"✗ Saved {reason} trader {wallet[:8]}...{wallet[-8:]} to database")
                except Exception as e:
                    # If it's an API error or stop processing, stop processing
                    if '429' in str(e) or 'rate limit' in str(e).lower() or 'stopped by user' in str(e).lower():
                        logging.error(f"API Error or stop signal: {e}. Stopping processor.")
                        self.stop_processing = True
                        if hasattr(self, 'fatal_callback') and self.fatal_callback:
                            self.fatal_callback(f"API Error or stop signal: {e}. Stopping processor.")
                        return
                    logging.error(f"Error evaluating trader {wallet[:8]}...{wallet[-8:]} for token {token_name}: {e}")
                    # Save error state with default values
                    error_stats = {
                        'total_bought_usd': 0.0,
                        'total_sold_usd': 0.0,
                        'realized_profit_usd': 0.0,
                        'realized_profit_ratio': '0.0x',
                        'currently_holding_amount': 0.0,
                        'total_buy_transactions': 0,
                        'total_sell_transactions': 0
                    }
                    self.save_trader_data(wallet, token_address, token_name, token_symbol, "ERROR", error_stats)
        total_traders = len(traders_data)
        completed = 0
        async def evaluate_with_progress(trader_data: dict, index: int):
            nonlocal completed
            try:
                if self.stop_processing:
                    return
                await evaluate_and_save(trader_data)
                completed += 1
                if completed % 10 == 0 or completed == total_traders:
                    logging.info(f"Progress: {completed}/{total_traders} ({completed/total_traders*100:.1f}%) - {passed_traders} passed so far")
                    # Send progress update to web app if callback exists
                    if hasattr(self, 'progress_callback') and self.progress_callback:
                        self.progress_callback(completed, total_traders, passed_traders)
            except Exception as e:
                completed += 1
                if '429' in str(e) or 'rate limit' in str(e).lower():
                    logging.error(f"API Error: {e}. Stopping processor.")
                    self.stop_processing = True
                    if hasattr(self, 'fatal_callback') and self.fatal_callback:
                        self.fatal_callback(f"API Error: {e}. Stopping processor.")
                    return
                logging.error(f"Error processing trader {index+1}/{total_traders}: {e}")
        tasks = [asyncio.create_task(evaluate_with_progress(t, i)) for i, t in enumerate(traders_data)]
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logging.error(f"Error during processing: {e}")
        self.mark_token_processed(token_address, token_name, token_symbol, len(traders_data), passed_traders)
        if self.stop_processing:
            logging.info(f"Processing stopped for token {token_name}")
            raise Exception("Processing stopped by user")
        return {
            "token_address": token_address,
            "token_name": token_name,
            "total_traders": len(traders_data),
            "passed_traders": passed_traders
        }
    
    async def process_all_tokens(self):
        """Process all unprocessed tokens (robust, persistent)."""
        tokens = self.load_tokens()
        processed_tokens = self.get_processed_token_addresses()
        unprocessed_tokens = [
            (addr, name) for addr, name in tokens 
            if addr not in processed_tokens
        ]
        
        if not unprocessed_tokens:
            logging.info("No new tokens to process! All tokens are already processed.")
            return
        
        logging.info(f"Found {len(unprocessed_tokens)} new tokens to process")
        logging.info(f"Skipping {len(tokens) - len(unprocessed_tokens)} already processed tokens")
        
        results = []
        for i, (token_address, token_name) in enumerate(unprocessed_tokens, 1):
            try:
                if self.stop_processing:
                    logging.info("Processing stopped by user")
                    break
                    
                logging.info(f"Processing token {i}/{len(unprocessed_tokens)}: {token_name}")
                result = await self.process_single_token(token_address, token_name)
                results.append(result)
                logging.info(f"Completed processing {token_name}: {result['passed_traders']}/{result['total_traders']} passed")
            except Exception as e:
                if '429' in str(e) or 'rate limit' in str(e).lower():
                    logging.error(f"API Error: {e}. Stopping processor.")
                    self.stop_processing = True
                    if hasattr(self, 'fatal_callback') and self.fatal_callback:
                        self.fatal_callback(f"API Error: {e}. Stopping processor.")
                    break
                logging.error(f"Failed to process token {token_name}: {e}")
                # Continue with next token even if one fails
        
        self.print_summary(results)
    
    def print_summary(self, results: List[Dict]):
        """Print processing summary."""
        print("\n" + "="*60)
        print("PROCESSING SUMMARY")
        print("="*60)
        if not results:
            print("No tokens were processed in this session.")
            return
        total_traders = sum(r['total_traders'] for r in results)
        total_passed = sum(r['passed_traders'] for r in results)
        for result in results:
            print(f"{result['token_name']:15} | "
                  f"Traders: {result['total_traders']:4d} | "
                  f"Passed: {result['passed_traders']:3d} | "
                  f"Rate: {(result['passed_traders']/result['total_traders']*100 if result['total_traders'] else 0):.1f}%")
        print("-"*60)
        if total_traders:
            print(f"TOTAL          | Traders: {total_traders:4d} | "
                  f"Passed: {total_passed:3d} | "
                  f"Rate: {total_passed/total_traders*100:.1f}%")
        else:
            print(f"TOTAL          | Traders: {total_traders:4d} | Passed: {total_passed:3d} | Rate: 0.0%")
        print("="*60)
    
    def get_database_stats(self):
        """Get statistics from the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get processed tokens count
            cursor.execute("SELECT COUNT(*) FROM processed_tokens")
            processed_tokens_count = cursor.fetchone()[0]
            
            # Get total traders evaluated
            cursor.execute("SELECT COUNT(*) FROM traders")
            total_traders = cursor.fetchone()[0]
            
            # Get passed traders count
            cursor.execute("SELECT COUNT(*) FROM traders WHERE evaluation_result = 'PASS'")
            passed_traders = cursor.fetchone()[0]
            
            # Get recent activity
            cursor.execute("""
                SELECT token_name, passed_traders, processed_at 
                FROM processed_tokens 
                ORDER BY processed_at DESC 
                LIMIT 5
            """)
            recent_activity = cursor.fetchall()
            
            return {
                "processed_tokens": processed_tokens_count,
                "total_traders": total_traders,
                "passed_traders": passed_traders,
                "recent_activity": recent_activity
            }

def main():
    processor = TokenProcessor()
    
    # Show current stats
    stats = processor.get_database_stats()
    print(f"Database Stats:")
    print(f"  Processed tokens: {stats['processed_tokens']}")
    print(f"  Total traders evaluated: {stats['total_traders']}")
    print(f"  Passed traders: {stats['passed_traders']}")
    
    if stats['recent_activity']:
        print(f"\nRecent activity:")
        for name, passed, timestamp in stats['recent_activity']:
            print(f"  {name}: {passed} passed at {timestamp}")
    
    # Process all tokens
    asyncio.run(processor.process_all_tokens())

if __name__ == "__main__":
    main() 