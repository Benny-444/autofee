#!/usr/bin/env python3
import json
import subprocess
from datetime import datetime, timedelta
import logging
import os
import configparser
from contextlib import contextmanager
import sqlite3

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_stagnant_wrapper.log'), level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Configuration constants
STAGNANT_RATIO_THRESHOLD = 0.20  # Channel must be above N% liquidity (0.1 = 10%)
STAGNANT_HOURS = 24              # Hours without routing to be considered stagnant
STAGNANT_REDUCTION_PCT = 0.5     # Reduce fees by N%
STAGNANT_STATE_FILE = os.path.expanduser('~/autofee/stagnant_state.json')
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')
FEE_DB_FILE = os.path.expanduser('~/autofee/fee_history.db')
CHAN_IDS = []  # Empty to process all channels
EXCLUDE_CHAN_IDS = []  # Add your channel IDs here

def load_stagnant_state():
    """Load persisted stagnant channel state"""
    try:
        if os.path.exists(STAGNANT_STATE_FILE):
            with open(STAGNANT_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading stagnant state: {str(e)}")
    return {}

def save_stagnant_state(state_data):
    """Save stagnant channel state with atomic write"""
    try:
        temp_file = STAGNANT_STATE_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(state_data, f, indent=2)
        os.replace(temp_file, STAGNANT_STATE_FILE)
    except Exception as e:
        logging.error(f"Error saving stagnant state: {str(e)}")

@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(FEE_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def check_recent_forwards(short_chan_id, hours_back):
    """Check if channel has any forwarding activity in the specified time period"""
    try:
        cutoff_time = int((datetime.now() - timedelta(hours=hours_back)).timestamp())

        with get_db() as conn:
            cursor = conn.execute(
                'SELECT COUNT(*) as forward_count FROM fee_history WHERE chan_id = ? AND timestamp >= ?',
                (str(short_chan_id), cutoff_time)
            )
            result = cursor.fetchone()
            forward_count = result['forward_count'] if result else 0

            logging.debug(f"Channel {short_chan_id}: {forward_count} forwards in last {hours_back} hours")
            return forward_count > 0

    except Exception as e:
        logging.error(f"Error checking forwards for channel {short_chan_id}: {str(e)}")
        return False

def run_lncli(args):
    """Execute lncli command and parse JSON output"""
    try:
        output = subprocess.check_output(['lncli'] + args, stderr=subprocess.STDOUT)
        result = json.loads(output.decode())
        if not result:
            logging.error(f"Empty lncli response for {args}: {result}")
            raise ValueError("Empty lncli response")
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"lncli command failed: {args}, error: {e.output.decode()}")
        raise
    except Exception as e:
        logging.error(f"Error running lncli {args}: {str(e)}")
        raise

def apply_stagnant_reduction(current_fee):
    """Apply stagnant reduction to a fee value"""
    if current_fee == 0:
        return 0

    abs_fee = abs(current_fee)

    # Calculate percentage reduction
    reduction = abs_fee * (STAGNANT_REDUCTION_PCT / 100)

    # Ensure minimum 1 ppm reduction
    reduction = max(1, round(reduction))

    new_abs_fee = max(0, abs_fee - reduction)

    # Preserve sign for negative fees
    return -new_abs_fee if current_fee < 0 else new_abs_fee

def scid_to_x_format(scid):
    """Convert decimal SCID to x format"""
    scid_int = int(scid)
    block_height = scid_int >> 40
    tx_index = (scid_int >> 16) & 0xFFFFFF
    output_index = scid_int & 0xFFFF
    return f"{block_height}x{tx_index}x{output_index}"

def identify_and_reduce_stagnant():
    """Identify stagnant channels and reduce their fees"""
    try:
        # Check if the INI file exists
        if not os.path.exists(CHARGE_INI_FILE):
            logging.error(f"INI file {CHARGE_INI_FILE} not found. Run autofee scripts first.")
            print(f"Error: INI file {CHARGE_INI_FILE} not found. Run autofee scripts first.")
            return

        # Load current state
        stagnant_state = load_stagnant_state()

        # Get all channels
        channels = run_lncli(['listchannels'])['channels']

        # Parse existing INI file
        config = configparser.ConfigParser()
        config.read(CHARGE_INI_FILE)

        updated_state = {}
        channels_processed = 0
        channels_stagnant = 0
        channels_newly_stagnant = 0
        channels_recovered = 0

        current_time = datetime.now()
        stagnant_cutoff = current_time - timedelta(hours=STAGNANT_HOURS)

        for chan in channels:
            chan_id = chan.get('chan_id')
            short_chan_id = chan.get('scid')

            # Skip if filtering by CHAN_IDS
            if CHAN_IDS and chan_id not in CHAN_IDS and str(short_chan_id) not in CHAN_IDS:
                continue
            if chan_id in EXCLUDE_CHAN_IDS or str(short_chan_id) in EXCLUDE_CHAN_IDS:
                logging.info(f"Skipping excluded channel {chan_id} (scid: {short_chan_id})")
                continue

            # Skip inactive channels
            if not chan.get('active', False):
                logging.info(f"Skipping inactive channel {chan_id}")
                continue

            # Calculate current liquidity ratio
            capacity = float(chan.get('capacity', 1))
            if capacity > 0:
                local_balance = float(chan.get('local_balance', 0))
                current_ratio = local_balance / capacity
            else:
                current_ratio = 0.5  # Default to middle if no capacity

            # Get previous state for this channel
            prev_state = stagnant_state.get(str(short_chan_id), {})
            prev_ratio = prev_state.get('last_ratio', current_ratio)
            last_change_str = prev_state.get('last_change', current_time.isoformat())
            last_change = datetime.fromisoformat(last_change_str)
            was_stagnant = prev_state.get('is_stagnant', False)

            # Check if channel has recent forwarding activity
            has_recent_forwards = check_recent_forwards(short_chan_id, STAGNANT_HOURS)

            if has_recent_forwards:
                # Channel has forwarding activity - reset timer and clear stagnant status
                last_change = current_time
                is_stagnant = False

                if was_stagnant:
                    channels_recovered += 1
                    logging.info(f"Channel {chan_id}: Recovered from stagnant state (had forwarding activity)")
            else:
                # No forwarding activity - use actual last forward time for calculation
                try:
                    with get_db() as conn:
                        cursor = conn.execute(
                            'SELECT MAX(timestamp) FROM fee_history WHERE chan_id = ?',
                            (str(short_chan_id),)
                        )
                        result = cursor.fetchone()
                        last_forward_timestamp = result[0] if result and result[0] else None

                    if last_forward_timestamp:
                        last_change = datetime.fromtimestamp(last_forward_timestamp)
                    else:
                        # Never forwarded - use a very old timestamp to ensure stagnant qualification
                        last_change = current_time - timedelta(days=30)
                        logging.info(f"Channel {chan_id}: No forwarding history found, using old timestamp for stagnant calculation")

                except Exception as e:
                    logging.error(f"Error getting last forward time for {chan_id}: {e}")
                    # Keep existing last_change as fallback
                    pass

                time_since_change = current_time - last_change

                if current_ratio > STAGNANT_RATIO_THRESHOLD and time_since_change > timedelta(hours=STAGNANT_HOURS):
                    is_stagnant = True
                    if not was_stagnant:
                        channels_newly_stagnant += 1
                        logging.info(f"Channel {chan_id}: Became stagnant (liquidity {current_ratio:.3f}, no forwards for {time_since_change.days}d {time_since_change.seconds//3600}h)")
                else:
                    is_stagnant = False

            # Update state
            updated_state[str(short_chan_id)] = {
                'last_ratio': current_ratio,
                'last_change': last_change.isoformat(),
                'is_stagnant': is_stagnant
            }

            # Apply fee reduction if stagnant
            if is_stagnant:
                channels_stagnant += 1

                # Find the section in the INI for this channel
                short_channel_id_x = scid_to_x_format(short_chan_id)
                section_name = f"autofee-{short_channel_id_x}"

                if config.has_section(section_name):
                    # Reduce outbound fee
                    if config.has_option(section_name, 'fee_ppm'):
                        current_outbound = int(float(config.get(section_name, 'fee_ppm')))
                        new_outbound = apply_stagnant_reduction(current_outbound)
                        config.set(section_name, 'fee_ppm', str(new_outbound))
                        logging.info(f"Channel {chan_id}: Reduced outbound fee from {current_outbound} to {new_outbound} ppm")

                    # Reduce inbound fee if present
                    if config.has_option(section_name, 'inbound_fee_ppm'):
                        current_inbound = int(float(config.get(section_name, 'inbound_fee_ppm')))
                        new_inbound = apply_stagnant_reduction(current_inbound)
                        if new_inbound == 0:
                            # Remove inbound fee if reduced to zero
                            config.remove_option(section_name, 'inbound_fee_ppm')
                            logging.info(f"Channel {chan_id}: Removed inbound fee (reduced from {current_inbound} to 0)")
                        else:
                            config.set(section_name, 'inbound_fee_ppm', str(new_inbound))
                            logging.info(f"Channel {chan_id}: Reduced inbound fee from {current_inbound} to {new_inbound} ppm")

                    # Add a comment to track stagnant status
                    if not config.has_option(section_name, '# stagnant'):
                        config.set(section_name, '# stagnant', 'true')
                else:
                    logging.warning(f"Channel {chan_id} is stagnant but has no section in INI")
            else:
                # Remove stagnant marker if it exists
                short_channel_id_x = scid_to_x_format(short_chan_id)
                section_name = f"autofee-{short_channel_id_x}"
                if config.has_section(section_name) and config.has_option(section_name, '# stagnant'):
                    config.remove_option(section_name, '# stagnant')

            channels_processed += 1

        # Save updated state
        save_stagnant_state(updated_state)

        # Write updated INI file with atomic write
        temp_file = CHARGE_INI_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            config.write(f)
        os.replace(temp_file, CHARGE_INI_FILE)

        logging.info(f"Processed {channels_processed} channels: {channels_stagnant} stagnant ({channels_newly_stagnant} new, {channels_recovered} recovered)")
        print(f"Processed {channels_processed} channels: {channels_stagnant} stagnant ({channels_newly_stagnant} new, {channels_recovered} recovered)")

    except Exception as e:
        logging.error(f"Error processing stagnant channels: {str(e)}")
        print(f"Error processing stagnant channels: {str(e)}")

if __name__ == "__main__":
    identify_and_reduce_stagnant()