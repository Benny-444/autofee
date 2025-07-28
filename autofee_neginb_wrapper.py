#!/usr/bin/env python3
import json
import subprocess
from datetime import datetime, timedelta
import logging
import os
import configparser

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_neginb_wrapper.log'), level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Configuration constants
NEGATIVE_INBOUND_TRIGGER = 30  # Apply when working range % < this
NEGATIVE_INBOUND_REMOVE = 60   # Remove when working range % > this
INITIAL_INBOUND_PCT = 50       # Initial % of avg_fee (as positive number, will be negated)
INCREMENT_PCT = 2              # Increment % of avg_fee per interval
MAX_INBOUND_PCT = 80           # Maximum % of avg_fee
AVG_FEE_FILE = os.path.expanduser('~/autofee/avg_fees.json')
NEGINB_STATE_FILE = os.path.expanduser('~/autofee/neginb_fees.json')
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')
CHAN_IDS = []  # Empty to process all channels
EXCLUDE_CHAN_IDS = [] # Add your channel IDs here

def load_avg_fees():
    """Load average fees from the outbound script's JSON file"""
    try:
        if os.path.exists(AVG_FEE_FILE):
            with open(AVG_FEE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading avg_fees: {str(e)}")
    return {}

def load_neginb_state():
    """Load persisted negative inbound fee state"""
    try:
        if os.path.exists(NEGINB_STATE_FILE):
            with open(NEGINB_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading neginb state: {str(e)}")
    return {}

def save_neginb_state(state_data):
    """Save negative inbound fee state with atomic write"""
    try:
        temp_file = NEGINB_STATE_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(state_data, f)
        os.replace(temp_file, NEGINB_STATE_FILE)
    except Exception as e:
        logging.error(f"Error saving neginb state: {str(e)}")

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

def calculate_neginb_fee(scid, working_range_pct, avg_fee, current_state):
    """Calculate negative inbound fee based on working range and state"""

    # Get current inbound fee and percentage from state
    current_inbound = current_state.get('inbound_fee', 0)
    current_pct = current_state.get('current_pct', 0)

    # Check if we should remove inbound fee
    if working_range_pct > NEGATIVE_INBOUND_REMOVE:
        if current_inbound < 0:  # Was active
            logging.info(f"Channel {scid}: Removing negative inbound fee (working range {working_range_pct:.1f}% > {NEGATIVE_INBOUND_REMOVE}%)")
        return 0, 0

    # Check if we should apply/increment inbound fee
    if working_range_pct < NEGATIVE_INBOUND_TRIGGER:
        if current_pct == 0:  # Not active, initialize
            new_pct = INITIAL_INBOUND_PCT
            new_inbound = -1 * round(avg_fee * new_pct / 100)
            logging.info(f"Channel {scid}: Initializing negative inbound fee to {new_inbound} ppm ({new_pct}% of avg_fee {avg_fee})")
        else:  # Already active, increment if not at max
            if current_pct < MAX_INBOUND_PCT:
                new_pct = min(current_pct + INCREMENT_PCT, MAX_INBOUND_PCT)
                new_inbound = -1 * round(avg_fee * new_pct / 100)
                logging.info(f"Channel {scid}: Incrementing negative inbound from {current_inbound} to {new_inbound} ppm ({current_pct}% -> {new_pct}% of avg_fee {avg_fee})")
            else:
                new_pct = current_pct
                new_inbound = -1 * round(avg_fee * new_pct / 100)
                logging.info(f"Channel {scid}: Keeping max negative inbound at {new_inbound} ppm ({new_pct}% of avg_fee {avg_fee})")
        return new_inbound, new_pct

    # In between thresholds - maintain percentage but recalculate based on current avg_fee
    if current_pct > 0:  # Has active inbound fee
        new_pct = current_pct
        new_inbound = -1 * round(avg_fee * new_pct / 100)
        if new_inbound != current_inbound:
            logging.info(f"Channel {scid}: Adjusting negative inbound from {current_inbound} to {new_inbound} ppm (maintaining {new_pct}% of avg_fee {avg_fee})")
        else:
            logging.info(f"Channel {scid}: Maintaining negative inbound at {new_inbound} ppm ({new_pct}% of avg_fee {avg_fee})")
        return new_inbound, new_pct

    # No active inbound fee and not triggered
    return 0, 0

def scid_to_x_format(scid):
    """Convert decimal SCID to x format"""
    scid_int = int(scid)
    block_height = scid_int >> 40
    tx_index = (scid_int >> 16) & 0xFFFFFF
    output_index = scid_int & 0xFFFF
    return f"{block_height}x{tx_index}x{output_index}"

def update_ini_with_inbound():
    """Update existing dynamic_charge.ini with inbound fees"""
    try:
        # Check if the INI file exists
        if not os.path.exists(CHARGE_INI_FILE):
            logging.error(f"INI file {CHARGE_INI_FILE} not found. Run autofee_wrapper.py first.")
            print(f"Error: INI file {CHARGE_INI_FILE} not found. Run autofee_wrapper.py first.")
            return

        # Load average fees from outbound script
        avg_fees = load_avg_fees()
        if not avg_fees:
            logging.warning("No average fees found. Run autofee_wrapper.py first.")
            return

        # Load current state
        neginb_state = load_neginb_state()

        # Get local node info
        local_info = run_lncli(['getinfo'])
        local_pubkey = local_info.get('identity_pubkey')
        if not local_pubkey:
            raise ValueError("Could not retrieve local pubkey")

        # Get all channels
        channels = run_lncli(['listchannels'])['channels']

        # Parse existing INI file
        config = configparser.ConfigParser()
        config.read(CHARGE_INI_FILE)

        updated_state = {}
        channels_updated = 0
        channels_with_inbound = 0

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

            # Get avg_fee for this channel
            avg_fee = avg_fees.get(str(short_chan_id), 0)
            if avg_fee == 0:
                logging.info(f"Skipping channel {chan_id} - no avg_fee data")
                continue

            # Calculate working range (simplified: using liquidity ratio as working range %)
            capacity = float(chan.get('capacity', 1))
            if capacity > 0:
                local_balance = float(chan.get('local_balance', 0))
                working_range_pct = (local_balance / capacity) * 100
            else:
                working_range_pct = 50  # Default to middle if no capacity

            # Get current state for this channel
            current_state = neginb_state.get(str(short_chan_id), {})

            # Calculate negative inbound fee
            inbound_fee, inbound_pct = calculate_neginb_fee(
                short_chan_id,
                working_range_pct,
                avg_fee,
                current_state
            )

            # Update state
            updated_state[str(short_chan_id)] = {
                'inbound_fee': inbound_fee,
                'current_pct': inbound_pct,
                'working_range_pct': working_range_pct,
                'avg_fee': avg_fee,
                'last_updated': datetime.now().isoformat()
            }

            # Find the section in the INI for this channel
            short_channel_id_x = scid_to_x_format(short_chan_id)
            section_name = f"autofee-{short_channel_id_x}"

            # Update the INI section if it exists
            if config.has_section(section_name):
                # Always set inbound_fee_ppm explicitly, even if 0
                config.set(section_name, 'inbound_fee_ppm', str(inbound_fee))

                if inbound_fee != 0:
                    channels_with_inbound += 1
                    logging.info(f"Channel {chan_id}: Set inbound_fee_ppm={inbound_fee} in section")
                else:
                    logging.info(f"Channel {chan_id}: Reset inbound_fee_ppm=0 in section")

                channels_updated += 1
            else:
                # Channel needs inbound but has no outbound section (rare case)
                if inbound_fee != 0:
                    config.add_section(section_name)
                    config.set(section_name, 'chan.id', str(short_chan_id))
                    config.set(section_name, 'strategy', 'static')
                    config.set(section_name, 'inbound_fee_ppm', str(inbound_fee))
                    channels_with_inbound += 1
                    channels_updated += 1
                    logging.info(f"Channel {chan_id}: Created new section with inbound_fee_ppm={inbound_fee}")

        # Save updated state
        save_neginb_state(updated_state)

        # Write updated INI file with atomic write
        temp_file = CHARGE_INI_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            config.write(f)
        os.replace(temp_file, CHARGE_INI_FILE)

        logging.info(f"Updated INI: {channels_updated} channels processed, {channels_with_inbound} with inbound fees")
        print(f"Updated INI: {channels_updated} channels processed, {channels_with_inbound} with inbound fees")

    except Exception as e:
        logging.error(f"Error updating INI with inbound fees: {str(e)}")
        print(f"Error updating INI with inbound fees: {str(e)}")

if __name__ == "__main__":
    update_ini_with_inbound()
