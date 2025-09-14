#!/usr/bin/env python3
import json
import subprocess
from datetime import datetime, timedelta
import logging
import os
import sqlite3
from contextlib import contextmanager

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_wrapper.log'), level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

ALPHA = 0.15
MIN_AVG_FEE = 10
DAYS_BACK = 14
ADJUSTMENT_FACTOR = 0.05         # Percentage of difference to apply as adjustment (0.1 = 10%)
AVG_FEE_FILE = os.path.expanduser('~/autofee/avg_fees.json')
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')
FEE_DB_FILE = os.path.expanduser('~/autofee/fee_history.db')
STAGNANT_STATE_FILE = os.path.expanduser('~/autofee/stagnant_state.json')  # Added
CHAN_IDS = [] # Leave empty for all channels
EXCLUDE_CHAN_IDS = []  # Add your channel IDs here

@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(FEE_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    """Initialize the fee history database"""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS fee_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chan_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                amt_out_msat INTEGER NOT NULL,
                fee_msat INTEGER NOT NULL,
                true_fee_msat INTEGER NOT NULL,
                true_fee_ppm REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_chan_timestamp ON fee_history(chan_id, timestamp)')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

        # Clean up old records (older than 14 days)
        cutoff_time = int((datetime.now() - timedelta(days=DAYS_BACK)).timestamp())
        conn.execute('DELETE FROM fee_history WHERE timestamp < ?', (cutoff_time,))
        conn.commit()

def get_last_timestamp():
    """Get the last processed timestamp from config table"""
    try:
        with get_db() as conn:
            cursor = conn.execute('SELECT value FROM config WHERE key = ?', ('last_ts',))
            result = cursor.fetchone()
            if result:
                return int(result['value'])
            # If no config entry exists, return 14 days ago
            return int((datetime.now() - timedelta(days=DAYS_BACK)).timestamp())
    except Exception as e:
        logging.error(f"Error getting last timestamp: {str(e)}")
        return int((datetime.now() - timedelta(days=DAYS_BACK)).timestamp())

def set_last_timestamp(timestamp):
    """Save the last processed timestamp to config table"""
    try:
        with get_db() as conn:
            conn.execute('INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)',
                        ('last_ts', str(timestamp), datetime.now()))
            conn.commit()
    except Exception as e:
        logging.error(f"Error saving last timestamp: {str(e)}")

def load_persisted_avg_fee(scid):
    """Load persisted avg_fee from JSON"""
    try:
        if os.path.exists(AVG_FEE_FILE):
            with open(AVG_FEE_FILE, 'r') as f:
                data = json.load(f)
                persisted = data.get(str(scid))
                if persisted is not None:
                    return persisted
    except Exception as e:
        logging.error(f"Error loading persisted avg_fee for {scid}: {str(e)}")
    return 0  # Default to 0 if no persisted, allowing init from current

def save_avg_fee(fee_data):
    """Save avg_fee for all channels to JSON with atomic write"""
    try:
        temp_file = AVG_FEE_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(fee_data, f)
        os.replace(temp_file, AVG_FEE_FILE)
    except Exception as e:
        logging.error(f"Error saving avg_fees: {str(e)}")

def load_stagnant_state():
    """Load stagnant state to check which channels are stagnant"""
    try:
        if os.path.exists(STAGNANT_STATE_FILE):
            with open(STAGNANT_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading stagnant state: {str(e)}")
    return {}

def run_lncli(args):
    """Execute lncli command and parse JSON output"""
    try:
        output = subprocess.check_output(['lncli'] + args, stderr=subprocess.STDOUT)
        result = json.loads(output.decode())
        if not result:
            logging.error(f"Empty lncli response for {args}: {result}")
            raise ValueError("Empty lncli response")
        if args[0] == 'fwdinghistory' and 'forwarding_events' not in result:
            logging.error(f"Invalid fwdinghistory response: {result}")
            raise ValueError("Invalid fwdinghistory response")
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"lncli command failed: {args}, error: {e.output.decode()}")
        raise
    except Exception as e:
        logging.error(f"Error running lncli {args}: {str(e)}")
        raise

def get_channel_info_at_time(chan_id, timestamp):
    """Get channel fee info at a specific time (returns defaults, unused in interim)"""
    return {
        'base_fee': 1000,
        'fee_rate': 100
    }

def update_fee_history(local_pubkey, channel_policies):
    """Fetch last 14 days of forwards and populate fee_history database"""
    try:
        last_ts = get_last_timestamp()
        current_time = int(datetime.now().timestamp())
        logging.info(f"Fetching forwards from last_ts={last_ts} to current_time={current_time}")

        forwards = run_lncli(['fwdinghistory', '--start_time', str(last_ts), '--end_time', str(current_time), '--max_events', '100000'])
        new_last_ts = last_ts
        processed_count = 0

        with get_db() as conn:
            for event in forwards.get('forwarding_events', []):
                chan_id_out = str(event.get('chan_id_out'))
                timestamp = int(event.get('timestamp', 0))
                amt_out_msat = int(event.get('amt_out_msat', 0))
                fee_msat = int(event.get('fee_msat', 0))

                if not chan_id_out or amt_out_msat <= 0:
                    logging.info(f"Skipping invalid forward: chan_id_out={chan_id_out}, amt_out_msat={amt_out_msat}")
                    continue

                new_last_ts = max(new_last_ts, timestamp)

                # Use cached channel policy instead of API call
                policy = channel_policies.get(chan_id_out)
                if not policy:
                    logging.warning(f"No policy found for channel {chan_id_out}, skipping forward")
                    continue

                # Calculate true_fee_msat using cached policy
                current_fee_rate = policy['local_fee_rate']
                base_fee = policy['local_base_fee']
                expected_fee_msat = int((amt_out_msat * (current_fee_rate / 1000000)) + base_fee)
                inbound_discount_msat = max(0, expected_fee_msat - fee_msat)
                true_fee_msat = fee_msat + inbound_discount_msat
                true_fee_ppm = (true_fee_msat / amt_out_msat) * 1_000_000 if amt_out_msat > 0 else 0

                conn.execute('''
                    INSERT INTO fee_history (chan_id, timestamp, amt_out_msat, fee_msat, true_fee_msat, true_fee_ppm)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (chan_id_out, timestamp, amt_out_msat, fee_msat, true_fee_msat, true_fee_ppm))

                processed_count += 1

            conn.commit()
            set_last_timestamp(new_last_ts)

        logging.info(f"Processed {processed_count} forwarding events")

    except Exception as e:
        logging.error(f"Error updating fee history: {str(e)}")

def calculate_avg_fee_from_history(scid, current_fee_ppm):
    """Calculate EMA from fee history database or return persisted if no history"""
    try:
        cutoff_time = int((datetime.now() - timedelta(days=DAYS_BACK)).timestamp())
        with get_db() as conn:
            cursor = conn.execute(
                '''SELECT timestamp, true_fee_ppm
                   FROM fee_history
                   WHERE chan_id = ? AND timestamp >= ?
                   ORDER BY timestamp ASC''',
                (scid, cutoff_time)
            )
            records = cursor.fetchall()
            
            if not records:
                persisted = load_persisted_avg_fee(scid)
                if persisted > 0:
                    return persisted
                # Check if channel exists in persisted data (even with 0 value)
                try:
                    if os.path.exists(AVG_FEE_FILE):
                        with open(AVG_FEE_FILE, 'r') as f:
                            data = json.load(f)
                            if str(scid) in data:
                                return data[str(scid)]  # Return existing value, even if 0
                except:
                    pass
                return current_fee_ppm  # Only for truly NEW channels
            
            # Rest of the function continues here for when records exist...
            ema = load_persisted_avg_fee(scid)
            for i, record in enumerate(records):
                true_fee_ppm = record['true_fee_ppm']
                if i == 0 and ema == 0:
                    # First forward and no persisted value - initialize EMA with first fee
                    ema = true_fee_ppm
                else:
                    # Normal EMA calculation
                    ema = ALPHA * true_fee_ppm + (1 - ALPHA) * ema

            ema = max(MIN_AVG_FEE, round(ema))  # Min only after calculation
            return ema

    except Exception as e:
        logging.error(f"Error calculating avg fee from history for {scid}: {str(e)}")
        return load_persisted_avg_fee(scid)

def get_channel_info(short_chan_id, local_pubkey):
    """Get channel info and extract local policy with correct field names"""
    try:
        if not short_chan_id:
            raise ValueError("No short channel ID provided")

        chan_info = run_lncli(['getchaninfo', short_chan_id])

        node1_pub = chan_info.get('node1_pub')
        node2_pub = chan_info.get('node2_pub')

        if node1_pub == local_pubkey:
            policy = chan_info.get('node1_policy', {})
        elif node2_pub == local_pubkey:
            policy = chan_info.get('node2_policy', {})
        else:
            logging.warning(f"No matching policy found for channel {short_chan_id}")
            return {
                'local_base_fee': 0,
                'local_fee_rate': 0,
                'current_fee_ppm': MIN_AVG_FEE
            }

        return {
            'local_base_fee': int(policy.get('fee_base_msat', 0)),
            'local_fee_rate': int(policy.get('fee_rate_milli_msat', 0)),
            'current_fee_ppm': int(policy.get('fee_rate_milli_msat', 0))
        }
    except Exception as e:
        logging.error(f"Error getting channel info for {short_chan_id}: {str(e)}")
        return {
            'local_base_fee': 0,
            'local_fee_rate': 0,
            'current_fee_ppm': MIN_AVG_FEE
        }

def generate_ini():
    """Generate dynamic_charge.ini with fees for all channels"""
    try:
        init_database()
        local_info = run_lncli(['getinfo'])
        local_pubkey = local_info.get('identity_pubkey')
        if not local_pubkey:
            raise ValueError("Could not retrieve local pubkey")
        channels = run_lncli(['listchannels'])['channels']
        processed_channels = 0
        skipped_stagnant = 0
        ini_content = ""
        fee_data = {}
        channel_policies = {}  # Cache for forwarding history processing

        # Load stagnant state
        stagnant_state = load_stagnant_state()

        # First pass: collect current_fee_ppm and build policy cache
        for chan in channels:
            chan_id = chan.get('chan_id')
            short_chan_id = chan.get('scid')
            # Skip if filtering by CHAN_IDS (support both chan_id and scid)
            if CHAN_IDS and chan_id not in CHAN_IDS and str(short_chan_id) not in CHAN_IDS:
                continue
            if chan_id in EXCLUDE_CHAN_IDS or str(short_chan_id) in EXCLUDE_CHAN_IDS:
                logging.info(f"Skipping excluded channel {chan_id} (scid: {short_chan_id})")
                continue
            # Skip Inactive Channels
            if not chan.get('active', False):
                logging.info(f"Skipping inactive channel {chan_id}")
                continue
            logging.info(f"Processing channel {chan_id} (scid: {short_chan_id})")
            channel_info = get_channel_info(short_chan_id, local_pubkey)
            current_fee = channel_info['current_fee_ppm']
            fee_data[str(chan['scid'])] = current_fee if current_fee > 0 else 0
            # Cache policy for forwarding history processing
            channel_policies[short_chan_id] = {
                'local_base_fee': channel_info['local_base_fee'],
                'local_fee_rate': channel_info['local_fee_rate']
            }

        # Initialize avg_fees.json on first run
        if not os.path.exists(AVG_FEE_FILE):
            save_avg_fee(fee_data)
            logging.info(f"Initialized avg_fees.json with current fees for {len(fee_data)} channels")

        # Update forwarding history with cached policies (prevents repeated API calls)
        update_fee_history(local_pubkey, channel_policies)

        # Calculate all updated avg_fees in batch
        updated_avg_fees = {}
        for chan in channels:
            chan_id = chan.get('chan_id')
            short_chan_id = chan.get('scid')
            if CHAN_IDS and chan_id not in CHAN_IDS and str(short_chan_id) not in CHAN_IDS:
                continue
            if chan_id in EXCLUDE_CHAN_IDS or str(short_chan_id) in EXCLUDE_CHAN_IDS:
                logging.info(f"Skipping excluded channel {chan_id} (scid: {short_chan_id})")
                continue
            # ADD THIS CHECK HERE TOO:
            if not chan.get('active', False):
                continue
            channel_info = get_channel_info(short_chan_id, local_pubkey)
            current_fee = channel_info['current_fee_ppm']
            avg_fee = calculate_avg_fee_from_history(chan['scid'], current_fee)
            updated_avg_fees[str(chan['scid'])] = avg_fee

        # Save all updated avg_fees at once
        save_avg_fee(updated_avg_fees)
        logging.info(f"Updated avg_fees for {len(updated_avg_fees)} channels")

        # Second pass: generate INI using updated avg_fees
        for chan in channels:
            chan_id = chan.get('chan_id')
            short_chan_id = chan.get('scid')
            if CHAN_IDS and chan_id not in CHAN_IDS and str(short_chan_id) not in CHAN_IDS:
                continue
            if chan_id in EXCLUDE_CHAN_IDS or str(short_chan_id) in EXCLUDE_CHAN_IDS:
                logging.info(f"Skipping excluded channel {chan_id} (scid: {short_chan_id})")
                continue

            # Skip Inactive Channels
            if not chan.get('active', False):
                continue
            
            # Check if channel is stagnant
            stagnant_info = stagnant_state.get(str(short_chan_id), {})
            is_stagnant = stagnant_info.get('is_stagnant', False)
            
            channel_info = get_channel_info(short_chan_id, local_pubkey)
            current_fee = channel_info['current_fee_ppm']
            avg_fee = updated_avg_fees.get(str(chan['scid']), load_persisted_avg_fee(chan['scid']))
            
            # Compute short_channel_id in x format from scid
            scid_int = int(chan['scid'])
            block_height = scid_int >> 40
            tx_index = (scid_int >> 16) & 0xFFFFFF
            output_index = scid_int & 0xFFFF
            short_channel_id_x = f"{block_height}x{tx_index}x{output_index}"
            
            if is_stagnant:
                # Skip stagnant channels - let stagnant wrapper handle them
                logging.info(f"Channel {chan_id}: Skipping stagnant channel (will be handled by stagnant wrapper) - current_fee={current_fee}, avg_fee={avg_fee}")
                skipped_stagnant += 1
                
                # Create basic entry with current fee (no adjustment)
                ini_content += f"[autofee-{short_channel_id_x}]\n"
                ini_content += f"chan.id = {chan['scid']}\n"
                ini_content += "strategy = static\n"
                ini_content += f"fee_ppm = {int(current_fee)}\n\n"
                processed_channels += 1
                continue
            
            channel_info = get_channel_info(short_chan_id, local_pubkey)
            current_fee = channel_info['current_fee_ppm']
            avg_fee = updated_avg_fees.get(str(chan['scid']), load_persisted_avg_fee(chan['scid']))
            capacity = float(chan.get('capacity', 1))
            if capacity > 0:
                ratio = float(chan.get('local_balance', 0)) / capacity
            else:
                ratio = 0.5
            set_fee = avg_fee * 2 * (1 - ratio)
            set_fee = max(0, round(set_fee))

            # Calculate adjustment with minimum ±1 ppm movement
            adjustment = ADJUSTMENT_FACTOR * (set_fee - current_fee)
            if adjustment != 0:
                adjustment = max(1, abs(round(adjustment))) * (1 if adjustment > 0 else -1)
            new_fee = current_fee + adjustment
            new_fee = max(0, new_fee)  # Ensure non-negative

            # Compute short_channel_id in x format from scid
            scid_int = int(chan['scid'])
            block_height = scid_int >> 40
            tx_index = (scid_int >> 16) & 0xFFFFFF
            output_index = scid_int & 0xFFFF
            short_channel_id_x = f"{block_height}x{tx_index}x{output_index}"

            ini_content += f"[autofee-{short_channel_id_x}]\n"
            ini_content += f"chan.id = {chan['scid']}\n"
            ini_content += "strategy = static\n"
            ini_content += f"fee_ppm = {int(new_fee)}\n\n"
            processed_channels += 1

            logging.info(f"Channel {chan_id}: avg_fee={avg_fee}, ratio={ratio:.2f}, current={current_fee}, target={set_fee}, new={new_fee}")

        with open(CHARGE_INI_FILE, 'w') as f:
            f.write(ini_content)

        logging.info(f"Generated INI for {processed_channels} channels (skipped {skipped_stagnant} stagnant channels)")
        print(f"Generated INI for {processed_channels} channels (skipped {skipped_stagnant} stagnant channels)")

    except Exception as e:
        logging.error(f"Error generating ini: {str(e)}")
        print(f"Error generating ini: {str(e)}")

if __name__ == "__main__":
    generate_ini()