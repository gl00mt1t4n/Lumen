#!/usr/bin/env python3
"""
web-app.py ──────────────────────────────────────────────────────────────────
Web frontend for the multi-coin trader processor.
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, Response, send_from_directory
import sqlite3
import json
import requests
from pathlib import Path
import sys
import os
import queue
import threading
import time
import logging

# ─── Ensure scrapers can be imported ─────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
PARENT_DIR = SCRIPT_DIR.resolve()  # Now we're in root, so same as SCRIPT_DIR
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

# Import the token manager functions directly
import importlib.util
spec = importlib.util.spec_from_file_location("token_manager", SCRIPT_DIR / "token-manager.py")
token_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(token_manager)

# Now we can use the functions
add_token = token_manager.add_token
fetch_token_name = token_manager.fetch_token_name

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'

DB_PATH = SCRIPT_DIR / "processed_traders.db"
TOKENS_FILE = SCRIPT_DIR / "tokens.txt"

# Global variables
log_queue = queue.Queue()
processing_active = False
processor_instance = None
current_processing_token = None
processing_stats = {
    'current_token': None,
    'total_tokens': 0,
    'processed_tokens': 0,
    'current_traders_processed': 0,
    'current_traders_total': 0,
    'passed_traders': 0,
    'start_time': None
}

# GMGN PASS FILTER LOGIC (as of 2024-06):
# 1. Exclude wallets with 'sandwich_bot' tag
# 2. 30-day PnL filter: PnL > 0.75 (75%)
# 3. ROI filter: At least one of top-3 holdings ROI >= 0.30 (30%)
# (See scrapers/gmgn.py:evaluate_trader)

def get_db_connection():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def log_message(message, token_name=None):
    """Add a message to the log queue with optional token name."""
    timestamp = time.strftime("%H:%M:%S")
    if token_name:
        log_entry = f"data: {timestamp} - [{token_name}] {message}\n\n"
    else:
        log_entry = f"data: {timestamp} - {message}\n\n"
    print(f"Adding log to queue: {log_entry.strip()}")  # Debug print
    log_queue.put(log_entry)

def update_processing_stats(stats_update):
    """Update processing statistics and broadcast to clients."""
    global processing_stats
    processing_stats.update(stats_update)
    # Broadcast stats update to clients
    log_queue.put(f"data: STATS_UPDATE: {json.dumps(processing_stats)}\n\n")

def categorize_error(error_message):
    """Categorize error messages for better labeling."""
    error_lower = error_message.lower()
    
    # API errors
    if any(keyword in error_lower for keyword in ['api', 'rate limit', 'timeout', 'connection', 'http', 'request failed']):
        return "API Error"
    
    # Bot/sandwich errors
    if any(keyword in error_lower for keyword in ['sandwich', 'bot', 'failed check', 'mev', 'frontrun']):
        return "Bot"
    
    # Default error
    return "Error"

@app.route('/')
def index():
    """Main dashboard - serve React template as static HTML."""
    return send_from_directory('templates', 'index.html')

@app.route('/add_token', methods=['GET', 'POST'])
def add_token_route():
    """Add token page."""
    if request.method == 'POST':
        token_address = request.form.get('token_address', '').strip()
        if token_address:
            try:
                # Fetch token name automatically
                token_name = fetch_token_name(token_address)
                add_token(token_address, token_name)
                return jsonify({'success': True, 'message': f'Token {token_name} added successfully!'})
            except Exception as e:
                return jsonify({'success': False, 'message': f'Error: {str(e)}'})
        else:
            return jsonify({'success': False, 'message': 'Token address is required'})
    
    return send_from_directory('templates', 'add_token.html')

@app.route('/view_data')
def view_data():
    """View database data."""
    return send_from_directory('templates', 'view_data.html')

@app.route('/logs')
def logs_page():
    """Render the real-time log viewer page."""
    return send_from_directory('templates', 'logs.html')

@app.route('/api/tokens')
def get_tokens():
    """Get list of tokens from tokens.txt with processing status."""
    tokens = []
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split(',')
                    if len(parts) >= 2:
                        token_address = parts[0].strip()
                        token_name = parts[1].strip()
                        
                        # Check if token is processed
                        is_processed = False
                        if DB_PATH.exists():
                            conn = get_db_connection()
                            cursor = conn.cursor()
                            cursor.execute("SELECT 1 FROM processed_tokens WHERE token_address = ?", (token_address,))
                            is_processed = cursor.fetchone() is not None
                            conn.close()
                        
                        tokens.append({
                            'address': token_address,
                            'name': token_name,
                            'processed': is_processed
                        })
    return jsonify(tokens)

@app.route('/api/traders')
def get_traders():
    """Get trader data from database grouped by wallet, with pagination support."""
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 50)  # cap at 50
    result_filter = request.args.get('filter', None)
    sort_by = request.args.get('sort_by', 'token_count')
    # New filters
    min_tokens = request.args.get('minTokens', type=float)
    max_tokens = request.args.get('maxTokens', type=float)
    min_passed = request.args.get('minPassed', type=float)
    max_passed = request.args.get('maxPassed', type=float)
    min_pnl = request.args.get('minPnl', type=float)
    max_pnl = request.args.get('maxPnl', type=float)
    min_winrate = request.args.get('minWinrate', type=float)
    max_winrate = request.args.get('maxWinrate', type=float)
    min_profit = request.args.get('minProfit', type=float)
    max_profit = request.args.get('maxProfit', type=float)
    min_txn_30d = request.args.get('minTxn30d', type=float)
    max_txn_30d = request.args.get('maxTxn30d', type=float)
    include_tags = request.args.get('include_tags', '').split(',') if request.args.get('include_tags') else []
    exclude_tags = request.args.get('exclude_tags', '').split(',') if request.args.get('exclude_tags') else []
    tokens_filter = request.args.get('tokens', '').split(',') if request.args.get('tokens') else []
    if not DB_PATH.exists():
        return jsonify({'traders': [], 'total': 0, 'page': page, 'per_page': per_page})
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT t.wallet_address, t.token_name, t.token_symbol, t.evaluation_result,
               t.pnl_pct_30d, t.winrate, t.realized_profit_usd, t.realized_profit_ratio,
               t.total_bought_usd, t.total_sold_usd, t.currently_holding_amount,
               t.total_buy_transactions, t.total_sell_transactions, t.tags,
               t.pnl_usd_7d, t.pnl_usd_30d, t.pnl_pct_7d, t.tx_7d, t.tx_30d
        FROM traders t
    """
    params = []
    if result_filter:
        query += " WHERE t.evaluation_result = ?"
        params.append(result_filter)
    cursor.execute(query, params)
    results = cursor.fetchall()
    wallet_groups = {}
    for row in results:
        wallet = row[0]
        if wallet not in wallet_groups:
            wallet_groups[wallet] = {
                'wallet_address': wallet,
                'tokens': [],
                'total_tokens': 0,
                'passed_tokens': 0,
                'avg_pnl_pct_30d': 0,
                'avg_winrate': 0,
                'total_realized_profit': 0,
                'txn_30d': 0,
                'tags': set()
            }
        token_data = {
            'token_name': row[1],
            'token_symbol': row[2],
            'evaluation_result': row[3],
            'pnl_pct_30d': row[4],
            'winrate': row[5],
            'realized_profit_usd': row[6],
            'realized_profit_ratio': row[7],
            'total_bought_usd': row[8],
            'total_sold_usd': row[9],
            'currently_holding_amount': row[10],
            'total_buy_transactions': row[11],
            'total_sell_transactions': row[12],
            'tags': json.loads(row[13]) if row[13] else [],
            'pnl_usd_7d': row[14],
            'pnl_usd_30d': row[15],
            'pnl_pct_7d': row[16],
            'tx_7d': row[17],
            'tx_30d': row[18]
        }
        wallet_groups[wallet]['tokens'].append(token_data)
        wallet_groups[wallet]['total_tokens'] += 1
        if row[3] == 'PASS':
            wallet_groups[wallet]['passed_tokens'] += 1
        wallet_groups[wallet]['avg_pnl_pct_30d'] += row[4] or 0
        wallet_groups[wallet]['avg_winrate'] += row[5] or 0
        wallet_groups[wallet]['total_realized_profit'] += row[6] or 0
        wallet_groups[wallet]['txn_30d'] += (row[17] or 0) + (row[18] or 0)
        if row[13]:
            tags = json.loads(row[13])
            wallet_groups[wallet]['tags'].update(tags)
        # Add evaluation result as a tag if it's not PASS
        if row[3] != 'PASS':
            wallet_groups[wallet]['tags'].add(row[3])
    for wallet in wallet_groups.values():
        if wallet['total_tokens'] > 0:
            wallet['avg_pnl_pct_30d'] = (wallet['avg_pnl_pct_30d'] / wallet['total_tokens']) * 100
            wallet['avg_winrate'] = (wallet['avg_winrate'] / wallet['total_tokens']) * 100
        wallet['tags'] = list(wallet['tags'])
    traders = list(wallet_groups.values())
    # Apply filters
    if min_tokens is not None:
        traders = [t for t in traders if t['total_tokens'] >= min_tokens]
    if max_tokens is not None:
        traders = [t for t in traders if t['total_tokens'] <= max_tokens]
    if min_passed is not None:
        traders = [t for t in traders if t['passed_tokens'] >= min_passed]
    if max_passed is not None:
        traders = [t for t in traders if t['passed_tokens'] <= max_passed]
    if min_pnl is not None:
        traders = [t for t in traders if t['avg_pnl_pct_30d'] >= min_pnl]
    if max_pnl is not None:
        traders = [t for t in traders if t['avg_pnl_pct_30d'] <= max_pnl]
    if min_winrate is not None:
        traders = [t for t in traders if t['avg_winrate'] >= min_winrate]
    if max_winrate is not None:
        traders = [t for t in traders if t['avg_winrate'] <= max_winrate]
    if min_profit is not None:
        traders = [t for t in traders if t['total_realized_profit'] >= min_profit]
    if max_profit is not None:
        traders = [t for t in traders if t['total_realized_profit'] <= max_profit]
    if min_txn_30d is not None:
        traders = [t for t in traders if t['txn_30d'] >= min_txn_30d]
    if max_txn_30d is not None:
        traders = [t for t in traders if t['txn_30d'] <= max_txn_30d]
    if include_tags:
        # Normalize tags for comparison (replace spaces with underscores)
        normalized_include_tags = [tag.replace(' ', '_') for tag in include_tags]
        traders = [t for t in traders if any(tag in t['tags'] for tag in normalized_include_tags)]
    if exclude_tags:
        # Normalize tags for comparison (replace spaces with underscores)
        normalized_exclude_tags = [tag.replace(' ', '_') for tag in exclude_tags]
        traders = [t for t in traders if not any(tag in t['tags'] for tag in normalized_exclude_tags)]
    if tokens_filter:
        traders = [t for t in traders if any(tok['token_name'] in tokens_filter for tok in t['tokens'])]
    total = len(traders)
    if sort_by == 'token_count':
        traders.sort(key=lambda x: x['total_tokens'], reverse=True)
    elif sort_by == 'pnl_pct_30d':
        traders.sort(key=lambda x: x['avg_pnl_pct_30d'], reverse=True)
    elif sort_by == 'winrate':
        traders.sort(key=lambda x: x['avg_winrate'], reverse=True)
    elif sort_by == 'realized_profit':
        traders.sort(key=lambda x: x['total_realized_profit'], reverse=True)
    elif sort_by == 'txn_30d':
        traders.sort(key=lambda x: x['txn_30d'], reverse=True)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = traders[start:end]
    conn.close()
    return jsonify({'traders': paginated, 'total': total, 'page': page, 'per_page': per_page})

@app.route('/api/stats')
def get_stats():
    """Get database statistics."""
    if not DB_PATH.exists():
        return jsonify({
            'processed_tokens': 0,
            'total_traders': 0,
            'passed_traders': 0,
            'pass_rate': 0,
            'token_breakdown': []
        })
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Overall stats - show ALL data from database
    cursor.execute("SELECT COUNT(DISTINCT token_address) FROM traders")
    processed_tokens = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM traders")
    total_traders = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM traders WHERE evaluation_result = 'PASS'")
    passed_traders = cursor.fetchone()[0]
    
    pass_rate = (passed_traders / total_traders * 100) if total_traders > 0 else 0
    
    # Token breakdown - show ALL processed tokens from processed_tokens table
    cursor.execute("""
        SELECT token_name, token_symbol, total_holders, passed_traders, processed_at
        FROM processed_tokens
        ORDER BY processed_at DESC
    """)
    
    token_breakdown = []
    for row in cursor.fetchall():
        token_breakdown.append({
            'token_name': row[0],
            'token_symbol': row[1],
            'total_holders': row[2],
            'passed_traders': row[3],
            'processed_at': row[4]
        })
    
    conn.close()
    
    return jsonify({
        'processed_tokens': processed_tokens,
        'total_traders': total_traders,
        'passed_traders': passed_traders,
        'pass_rate': round(pass_rate, 1),
        'token_breakdown': token_breakdown
    })

@app.route('/api/processing-stats')
def get_processing_stats():
    """Get current processing statistics."""
    global processing_stats
    return jsonify(processing_stats)

@app.route('/api/logs')
def stream_logs():
    """Stream logs via Server-Sent Events."""
    def generate():
        while True:
            try:
                # Get message from queue with timeout
                message = log_queue.get(timeout=1)
                yield message
            except queue.Empty:
                # Send keepalive
                yield "data: \n\n"
    
    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/run_processor', methods=['POST'])
def run_processor():
    """Run the multi-coin processor with real-time logging."""
    global processing_active, current_processing_token, processing_stats
    
    if processing_active:
        return jsonify({'success': False, 'message': 'Processor is already running'})
    
    processing_active = True
    
    # Initialize processing stats
    processing_stats.update({
        'current_token': None,
        'total_tokens': 0,
        'processed_tokens': 0,
        'current_traders_processed': 0,
        'current_traders_total': 0,
        'passed_traders': 0,
        'start_time': time.time()
    })
    
    try:
        log_message("Starting processor...")
        
        # Import and run the processor using absolute imports
        import importlib.util
        spec = importlib.util.spec_from_file_location("multi_coin_processor", SCRIPT_DIR / "multi-coin-processor.py")
        multi_coin_processor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(multi_coin_processor)
        
        # Create a custom logging handler that sends to our queue
        class QueueHandler(logging.Handler):
            def emit(self, record):
                if processing_active:  # Only log if processing is still active
                    log_entry = self.format(record)
                    # Categorize errors for better labeling
                    if record.levelname == 'ERROR':
                        error_category = categorize_error(log_entry)
                        log_entry = f"[{error_category}] {log_entry}"
                    log_message(log_entry, current_processing_token)
        
        # Set up logging to go to our queue, only once
        logger = logging.getLogger()
        queue_handler = QueueHandler()
        queue_handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
        # Use the new attach_logging_handler_once method
        global processor_instance
        processor_instance = multi_coin_processor.TokenProcessor()
        processor_instance.attach_logging_handler_once(queue_handler)
        logger.setLevel(logging.INFO)
        
        log_message("Initializing processor...")
        
        # Set up token tracking with enhanced stats
        def update_current_token(token_name):
            global current_processing_token, processing_stats
            current_processing_token = token_name
            processing_stats['current_token'] = token_name
            processing_stats['processed_tokens'] += 1
            update_processing_stats(processing_stats)
            log_message(f"Starting processing for token: {token_name}")
        
        # Set up progress tracking
        def update_progress(completed, total, passed):
            global processing_stats
            processing_stats['current_traders_processed'] = completed
            processing_stats['current_traders_total'] = total
            processing_stats['passed_traders'] = passed
            update_processing_stats(processing_stats)
        
        # Set up fatal error callback
        def fatal_callback(msg):
            global processing_active, processor_instance
            log_message(f"FATAL: {msg}")
            processing_active = False
            if processor_instance:
                processor_instance.stop()
                processor_instance = None
        processor_instance.set_token_callback(update_current_token)
        processor_instance.set_progress_callback(update_progress)
        processor_instance.set_fatal_callback(fatal_callback)
        
        # Get total tokens to process
        tokens = []
        if TOKENS_FILE.exists():
            with open(TOKENS_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split(',')
                        if len(parts) >= 2:
                            tokens.append((parts[0].strip(), parts[1].strip()))
        
        processing_stats['total_tokens'] = len(tokens)
        update_processing_stats(processing_stats)
        
        log_message(f"Processing {len(tokens)} tokens...")
        import asyncio
        asyncio.run(processor_instance.process_all_tokens())
        
        if processing_active:  # Only show success if not stopped
            log_message("Processing completed successfully!")
            return jsonify({'success': True, 'message': 'Processing completed successfully!'})
        else:
            log_message("Processing stopped by user")
            return jsonify({'success': True, 'message': 'Processing stopped by user'})
    except Exception as e:
        log_message(f"Error: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        processing_active = False
        current_processing_token = None
        # Reset processing stats
        processing_stats.update({
            'current_token': None,
            'total_tokens': 0,
            'processed_tokens': 0,
            'current_traders_processed': 0,
            'current_traders_total': 0,
            'passed_traders': 0,
            'start_time': None
        })

# Add endpoints for processing status and stop
@app.route('/api/processing_status')
def processing_status():
    global processing_active
    return jsonify({'processing': processing_active})

@app.route('/api/stop_processor', methods=['POST'])
def stop_processor():
    global processing_active, processor_instance
    if processing_active and processor_instance:
        try:
            processor_instance.stop()
            processing_active = False
            processor_instance = None
            log_message("Processor stopped by user.")
            return jsonify({'success': True, 'message': 'Processor stopped.'})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})
    return jsonify({'success': False, 'message': 'Processor not running.'})

@app.route('/api/preview_token', methods=['POST'])
def preview_token():
    """Preview token info before adding."""
    token_address = request.json.get('token_address', '').strip()
    if not token_address:
        return jsonify({'error': 'Token address is required'}), 400
    
    try:
        token_name = fetch_token_name(token_address)
        return jsonify({
            'name': token_name,
            'symbol': token_name.split()[0] if token_name != 'UNKNOWN' else 'UNKNOWN'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/csv')
def export_csv():
    """Export trader data to CSV."""
    result_filter = request.args.get('filter', None)
    
    if not DB_PATH.exists():
        return jsonify({'error': 'No database found'}), 404
    
    import csv
    from io import StringIO
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT t.wallet_address, pt.token_name, t.evaluation_result,
               t.pnl_pct_30d, t.winrate, t.realized_profit, t.realized_profit_ratio,
               t.total_bought_usd, t.total_sold_usd, t.currently_holding_amount,
               t.total_buy_transactions, t.total_sell_transactions, t.tags,
               t.pnl_usd_7d, t.pnl_usd_30d, t.pnl_pct_7d, t.tx_7d, t.tx_30d
        FROM traders t
        JOIN processed_tokens pt ON t.token_address = pt.token_address
    """
    
    params = []
    if result_filter:
        query += " WHERE t.evaluation_result = ?"
        params.append(result_filter)
    
    query += " ORDER BY t.pnl_pct_30d DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    
    # Create CSV
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Wallet Address', 'Token Name', 'Token Symbol', 'Evaluation Result',
        '30d PnL %', 'Winrate %', 'Realized Profit USD', 'Realized Profit Ratio',
        'Total Bought USD', 'Total Sold USD', 'Currently Holding Amount',
        'Total Buy Transactions', 'Total Sell Transactions', 'Tags',
        '7d PnL USD', '30d PnL USD', '7d PnL %', '7d Transactions', '30d Transactions'
    ])
    
    # Write data
    for row in results:
        writer.writerow(row)
    
    conn.close()
    
    from flask import Response
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=traders_export.csv'}
    )

@app.route('/api/export/json')
def export_json():
    """Export trader data to JSON."""
    result_filter = request.args.get('filter', None)
    
    if not DB_PATH.exists():
        return jsonify({'error': 'No database found'}), 404
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT t.wallet_address, pt.token_name, t.evaluation_result,
               t.pnl_pct_30d, t.winrate, t.realized_profit, t.realized_profit_ratio,
               t.total_bought_usd, t.total_sold_usd, t.currently_holding_amount,
               t.total_buy_transactions, t.total_sell_transactions, t.tags,
               t.pnl_usd_7d, t.pnl_usd_30d, t.pnl_pct_7d, t.tx_7d, t.tx_30d
        FROM traders t
        JOIN processed_tokens pt ON t.token_address = pt.token_address
    """
    
    params = []
    if result_filter:
        query += " WHERE t.evaluation_result = ?"
        params.append(result_filter)
    
    query += " ORDER BY t.pnl_pct_30d DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    
    traders = []
    for row in results:
        trader = dict(row)
        # Parse JSON fields
        if trader['tags']:
            trader['tags'] = json.loads(trader['tags'])
        else:
            trader['tags'] = []
        traders.append(trader)
    
    conn.close()
    return jsonify(traders)

@app.route('/api/remove_token', methods=['POST'])
def remove_token():
    """Remove token from tokens.txt file and delete all associated data from database."""
    data = request.get_json()
    token_address = data.get('token_address')
    if not token_address:
        return jsonify({'success': False, 'message': 'No token_address provided'}), 400
    
    try:
        # Remove from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM processed_tokens WHERE token_address = ?", (token_address,))
        cursor.execute("DELETE FROM traders WHERE token_address = ?", (token_address,))
        conn.commit()
        conn.close()
        
        # Remove from tokens.txt file
        if TOKENS_FILE.exists():
            with open(TOKENS_FILE, 'r') as f:
                lines = f.readlines()
            
            # Filter out the line containing this token address
            filtered_lines = []
            for line in lines:
                if not line.strip().startswith(token_address):
                    filtered_lines.append(line)
            
            # Write back to file
            with open(TOKENS_FILE, 'w') as f:
                f.writelines(filtered_lines)
        
        return jsonify({'success': True, 'message': 'Token completely removed from system'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) 