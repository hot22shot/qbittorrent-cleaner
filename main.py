import os
import time
import logging
import qbittorrentapi

# --- Configuration ---
QBIT_HOST = os.getenv("QBIT_HOST", "http://qbittorrent:8080")
QBIT_USER = os.getenv("QBIT_USER", "admin")
QBIT_PASS = os.getenv("QBIT_PASS", "adminadmin")

# Format: "category|seconds|ratio|volume_bytes,category2|...|...|..."
RULES_ENV = os.getenv("PURGE_RULES", "")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 3600))
DELETE_FILES = os.getenv("DELETE_FILES", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def parse_rules(env_string):
    """
    Parses the rule string into a dictionary.
    Input: "radarr|172800|2.0|-1, sonarr|86400|-1|-1"
    Output: {'radarr': {'time': 172800, 'ratio': 2.0, 'vol': -1}, ...}
    """
    rules = {}
    if not env_string:
        return rules

    # Split by comma to get each category block
    blocks = [b.strip() for b in env_string.split(',')]
    
    for block in blocks:
        try:
            parts = block.split('|')
            if len(parts) != 4:
                logger.warning(f"Skipping malformed rule (needs 4 parts): {block}")
                continue
            
            cat_name = parts[0].strip()
            min_time = int(parts[1])
            min_ratio = float(parts[2])
            min_vol = int(parts[3])
            
            rules[cat_name] = {
                'time': min_time,
                'ratio': min_ratio,
                'vol': min_vol
            }
        except ValueError as e:
            logger.error(f"Error parsing rule block '{block}': {e}")
            
    return rules

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
        # Check if this torrent's category is in our rules
        cat = torrent.category
        if cat not in rules:
            continue # Ignore categories we haven't defined rules for

        rule = rules[cat]
        
        # 1. Check Completion
        if torrent.amount_left > 0:
            continue

        # 2. Check Time (Mandatory)
        seed_time = torrent.get('seeding_time')
        if not seed_time and torrent.get('completion_on'):
            seed_time = time.time() - torrent.completion_on
        
        if seed_time < rule['time']:
            continue

        # 3. Check Ratio OR Volume
        # If both are -1, we only care about time (which passed)
        req_ratio = rule['ratio']
        req_vol = rule['vol']
        
        if req_ratio < 0 and req_vol < 0:
            should_delete = True
        else:
            ratio_met = (req_ratio > 0 and torrent.ratio >= req_ratio)
            vol_met = (req_vol > 0 and torrent.uploaded >= req_vol)
            should_delete = ratio_met or vol_met

        if should_delete:
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
    
    # Parse rules once on startup
    active_rules = parse_rules(RULES_ENV)
    logger.info(f"Loaded Rules: {active_rules}")
    
    if not active_rules:
        logger.warning("No rules defined in PURGE_RULES variable! Script will effectively do nothing.")

    while True:
        if active_rules:
            qbt = get_client()
            if qbt:
                process_torrents(qbt, active_rules)
        
        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)
