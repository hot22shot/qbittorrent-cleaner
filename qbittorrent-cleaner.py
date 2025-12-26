import time
import logging
import yaml
import qbittorrentapi
from pathlib import Path

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger()

CONFIG_PATH = "/app/config/config.yaml"

def load_config():
    if not Path(CONFIG_PATH).exists():
        logger.error(f"Config file not found at {CONFIG_PATH}")
        return None
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)

def get_client(cfg):
    try:
        client = qbittorrentapi.Client(
            host=cfg['qbit_host'], 
            username=cfg['qbit_user'], 
            password=cfg['qbit_pass']
        )
        client.auth_log_in()
        return client
    except Exception as e:
        logger.error(f"Failed to connect to qBittorrent: {e}")
        return None

def check_rules(torrent, rules):
    """
    Returns True if torrent should be deleted based on rules.
    """
    min_time = rules.get('min_seed_time', 0)
    min_ratio = rules.get('min_ratio', -1)
    min_vol = rules.get('min_volume_bytes', -1)

    # 1. Check Time (Mandatory)
    # Calculate seed time manually if API doesn't provide it
    seed_time = torrent.get('seeding_time')
    if not seed_time and torrent.get('completion_on'):
        seed_time = time.time() - torrent.completion_on
    
    if seed_time < min_time:
        return False

    # 2. Check Ratio OR Volume
    # If both are -1, we only cared about time (which passed), so delete.
    if min_ratio < 0 and min_vol < 0:
        return True
    
    ratio_met = (min_ratio > 0 and torrent.ratio >= min_ratio)
    vol_met = (min_vol > 0 and torrent.uploaded >= min_vol)

    return ratio_met or vol_met

def process_torrents(client, config):
    logger.info("Starting torrent check...")
    
    # Reload settings in case they changed
    settings = config['settings']
    rules_map = config['rules']
    
    try:
        torrents = client.torrents_info()
    except Exception as e:
        logger.error(f"Failed to retrieve torrents: {e}")
        return

    to_delete = []

    for torrent in torrents:
        # Skip incomplete
        if torrent.amount_left > 0:
            continue

        cat = torrent.category
        rule_set = None

        # Determine which rules apply
        if cat in rules_map:
            rule_set = rules_map[cat]
        elif 'default' in rules_map:
            rule_set = rules_map['default']
        
        # If no rule set matches (and no default), skip
        if not rule_set:
            continue

        if check_rules(torrent, rule_set):
            to_delete.append(torrent)

    if not to_delete:
        logger.info("No torrents matched deletion criteria.")
        return

    hashes = [t.hash for t in to_delete]
    names = [f"{t.name} ({t.category})" for t in to_delete]
    
    logger.info(f"Found {len(hashes)} torrents to purge: {names}")

    if settings.get('dry_run', True):
        logger.info("[DRY RUN] No actions taken.")
    else:
        client.torrents_delete(torrent_hashes=hashes, delete_files=settings.get('delete_files', False))
        logger.info(f"Deleted {len(hashes)} torrents.")

if __name__ == "__main__":
    while True:
        config = load_config()
        if config:
            qbt = get_client(config['settings'])
            if qbt:
                process_torrents(qbt, config)
            
            interval = config['settings'].get('check_interval', 3600)
            logger.info(f"Sleeping for {interval} seconds...")
            time.sleep(interval)
        else:
            logger.error("Config failed to load. Retrying in 60s...")
            time.sleep(60)
