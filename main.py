import os
import time
import logging
import qbittorrentapi

# --- Configuration ---
QBIT_HOST = os.getenv("QBIT_HOST", "http://qbittorrent:8080")
QBIT_USER = os.getenv("QBIT_USER", "admin")
QBIT_PASS = os.getenv("QBIT_PASS", "adminadmin")

# Format: "category|expression, category|expression"
RULES_ENV = os.getenv("PURGE_RULES", "")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 3600))
DELETE_FILES = os.getenv("DELETE_FILES", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def parse_rules(env_string):
    """
    Parses "cat|expr, cat2|expr2" into a dictionary.
    """
    rules = {}
    if not env_string:
        return rules

    # Split by comma for different categories
    # We use a primitive split, assuming users won't put commas inside their math expressions
    blocks = [b.strip() for b in env_string.split(',')]
    
    for block in blocks:
        if '|' not in block:
            continue
        
        try:
            # Split only on the first pipe to separate Category from Logic
            cat_name, expression = block.split('|', 1)
            rules[cat_name.strip()] = expression.strip()
        except ValueError:
            logger.warning(f"Skipping malformed rule: {block}")
            
    return rules

def evaluate_rule(expression, stats):
    """
    Safely evaluates a boolean string like 'time > 60 and ratio > 1.0'
    using the stats dictionary provided.
    """
    allowed_names = {
        "time": stats['time'],         # Seeding time in seconds
        "ratio": stats['ratio'],       # Share ratio
        "uploaded": stats['uploaded'], # Uploaded bytes
        "size": stats['size'],         # Total size of torrent (bytes)
        "seeds": stats['seeds']        # Number of connected seeds
    }
    
    try:
        # We use eval() but strictly limit the scope to variables above + math logic.
        # __builtins__: None prevents access to system functions.
        return eval(expression, {"__builtins__": None}, allowed_names)
    except Exception as e:
        logger.error(f"Rule Logic Error in expression '{expression}': {e}")
        return False

def get_client():
    try:
        client = qbittorrentapi.Client(host=QBIT_HOST, username=QBIT_USER, password=QBIT_PASS)
        client.auth_log_in()
        return client
    except Exception as e:
        logger.error(f"Failed to connect to qBittorrent: {e}")
        return None

def process_torrents(client, rules):
    logger.info("Starting torrent check...")
    
    try:
        torrents = client.torrents_info()
    except Exception as e:
        logger.error(f"Failed to retrieve torrents: {e}")
        return

    to_delete = []

    for torrent in torrents:
        cat = torrent.category
        
        # 1. Check if we have a rule for this category
        if cat not in rules:
            continue

        rule_expression = rules[cat]
        
        # 2. Check Completion
        if torrent.amount_left > 0:
            continue

        # 3. Gather Stats
        seed_time = torrent.get('seeding_time')
        if not seed_time and torrent.get('completion_on'):
            seed_time = time.time() - torrent.completion_on
        
        stats = {
            'time': seed_time,
            'ratio': torrent.ratio,
            'uploaded': torrent.uploaded,
            'size': torrent.total_size,
            'seeds': torrent.num_seeds
        }

        # 4. Evaluate the Logic
        if evaluate_rule(rule_expression, stats):
            to_delete.append(torrent)

    # Execute
    if not to_delete:
        logger.info("No torrents found matching removal criteria.")
        return

    hashes = [t.hash for t in to_delete]
    names = [f"{t.name} ({t.category})" for t in to_delete]

    logger.info(f"Found {len(hashes)} torrents to purge: {names}")

    if DRY_RUN:
        logger.info("[DRY RUN] No actions taken.")
    else:
        try:
            client.torrents_delete(torrent_hashes=hashes, delete_files=DELETE_FILES)
            logger.info(f"Successfully deleted {len(hashes)} torrents.")
        except Exception as e:
            logger.error(f"Error deleting torrents: {e}")

if __name__ == "__main__":
    logger.info("qBittorrent Auto-Purger Started")
    
    active_rules = parse_rules(RULES_ENV)
    logger.info(f"Loaded Rules: {active_rules}")
    
    while True:
        if active_rules:
            qbt = get_client()
            if qbt:
                process_torrents(qbt, active_rules)
        
        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)
