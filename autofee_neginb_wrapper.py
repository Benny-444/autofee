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
NEGATIVE_INBOUND_TRIGGER = 20   # Apply when drops below this; maintain between this and REMOVE
NEGATIVE_INBOUND_REMOVE = 40    # Remove when goes above this
MAX_REMOTE_FEE_FOR_INBOUND = 2  # Max remote outbound fee (ppm) to qualify for neg inbound
EXCLUDE_REMOTE_FEE_CHECK = []  # Channel IDs to exclude from remote fee requirement
# Maintenance zone: NEGATIVE_INBOUND_TRIGGER to NEGATIVE_INBOUND_REMOVE (30-60%)
INITIAL_INBOUND_PCT = 30       # Initial % of avg_fee (as positive number, will be negated)
INCREMENT_PCT = 1              # Increment % of avg_fee per interval
MAX_INBOUND_PCT = 70           # Maximum % of avg_fee
AVG_FEE_FILE = os.path.expanduser('~/autofee/avg_fees.json')
NEGINB_STATE_FILE = os.path.expanduser('~/autofee/neginb_fees.json')
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')
CHAN_IDS = []  # Empty to process all channels
EXCLUDE_CHAN_IDS = []

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

def get_remote_outbound_fee(short_chan_id, local_pubkey):
    """Get remote peer's outbound fee rate"""
    try:
        chan_info = run_lncli(['getchaninfo', short_chan_id])

        # Determine which policy is the remote peer's
        if chan_info.get('node1_pub') == local_pubkey:
            # We are node1, so remote is node2
            remote_policy = chan_info.get('node2_policy', {})
        else:
            # We are node2, so remote is node1
            remote_policy = chan_info.get('node1_policy', {})

        return int(remote_policy.get('fee_rate_milli_msat', 999999))  # Default high if not found
    except:
        return 999999  # Return high value if cannot determine, disqualifying the channel

def calculate_neginb_fee(scid, working_range_pct, avg_fee, current_state, local_pubkey):
    """Calculate negative inbound fee based on working range and state"""

    # Get current inbound fee and percentage from state
    current_inbound = current_state.get('inbound_fee', 0)
    current_pct = current_state.get('current_pct', 0)
    has_been_above_threshold = current_state.get('has_been_above_threshold', False)

    # First, check if channel has ever been above threshold
    if working_range_pct > NEGATIVE_INBOUND_TRIGGER:
        has_been_above_threshold = True

    # Check if we should remove inbound fee
    if working_range_pct > NEGATIVE_INBOUND_REMOVE:
        if current_inbound < 0:  # Was active
            logging.info(f"Channel {scid}: Removing negative inbound fee (working range {working_range_pct:.1f}% > {NEGATIVE_INBOUND_REMOVE}%)")
        return 0, 0, has_been_above_threshold

    # Check if we should apply/increment inbound fee
    if working_range_pct < NEGATIVE_INBOUND_TRIGGER and has_been_above_threshold:
        # Only apply negative inbound if channel has previously been above threshold

        # Check remote fee FIRST - applies to both initialization AND incrementation
        if str(scid) not in EXCLUDE_REMOTE_FEE_CHECK:
            remote_fee = get_remote_outbound_fee(scid, local_pubkey)
            if remote_fee > MAX_REMOTE_FEE_FOR_INBOUND:
                logging.info(f"Channel {scid}: Remote fee {remote_fee} ppm exceeds max {MAX_REMOTE_FEE_FOR_INBOUND} ppm, not applying/incrementing negative inbound")
                return 0, 0, has_been_above_threshold
            # Log that remote fee is acceptable
            logging.info(f"Channel {scid}: Remote fee {remote_fee} ppm is acceptable (max {MAX_REMOTE_FEE_FOR_INBOUND} ppm)")
        else:
            logging.info(f"Channel {scid}: Excluded from remote fee check, proceeding with negative inbound")

        if current_pct == 0:  # Not active, initialize
            # Remote fee already checked above, proceed with initialization
            new_pct = INITIAL_INBOUND_PCT
            new_inbound = -1 * int(round(avg_fee * new_pct / 100))
            logging.info(f"Channel {scid}: Initializing negative inbound fee to {new_inbound} ppm ({new_pct}% of avg_fee {avg_fee}) - channel dropped below threshold")
        else:  # Already active, increment if not at max
            # Remote fee already checked above, proceed with incrementation
            if current_pct < MAX_INBOUND_PCT:
                new_pct = min(current_pct + INCREMENT_PCT, MAX_INBOUND_PCT)
                new_inbound = -1 * int(round(avg_fee * new_pct / 100))
                logging.info(f"Channel {scid}: Incrementing negative inbound from {current_inbound} to {new_inbound} ppm ({current_pct}% -> {new_pct}% of avg_fee {avg_fee})")
            else:
                new_pct = current_pct
                new_inbound = -1 * int(round(avg_fee * new_pct / 100))
                logging.info(f"Channel {scid}: Keeping max negative inbound at {new_inbound} ppm ({new_pct}% of avg_fee {avg_fee})")
        return new_inbound, new_pct, has_been_above_threshold
    elif working_range_pct < NEGATIVE_INBOUND_TRIGGER and not has_been_above_threshold:
        # Channel is below threshold but has never been above - don't apply negative inbound
        logging.info(f"Channel {scid}: Below threshold ({working_range_pct:.1f}%) but never been above - not applying negative inbound")
        return 0, 0, has_been_above_threshold

    # In between thresholds - maintain percentage but recalculate based on current avg_fee
    if current_pct > 0:  # Has active inbound fee
        new_pct = current_pct
        new_inbound = -1 * int(round(avg_fee * new_pct / 100))
        if new_inbound != current_inbound:
            logging.info(f"Channel {scid}: Adjusting negative inbound from {current_inbound} to {new_inbound} ppm (maintaining {new_pct}% of avg_fee {avg_fee})")
        else:
            logging.info(f"Channel {scid}: Maintaining negative inbound at {new_inbound} ppm ({new_pct}% of avg_fee {avg_fee})")
        return new_inbound, new_pct, has_been_above_threshold

    # No active inbound fee and not triggered
    return 0, 0, has_been_above_threshold

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

        # Start with existing state instead of empty dict
        updated_state = dict(neginb_state)  # Preserve all existing state

        # Parse existing INI file
        config = configparser.ConfigParser()
        config.read(CHARGE_INI_FILE)

        channels_updated = 0
        channels_with_inbound = 0
        channels_never_above = 0
        channels_remote_fee_too_high = 0  # Track how many blocked by remote fee

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

            # Calculate negative inbound fee (now passing local_pubkey)
            inbound_fee, inbound_pct, has_been_above_threshold = calculate_neginb_fee(
                short_chan_id,
                working_range_pct,
                avg_fee,
                current_state,
                local_pubkey  # Pass local_pubkey for remote fee check
            )

            # Count channels that have never been above threshold
            if not has_been_above_threshold:
                channels_never_above += 1

            # Track if this channel was blocked by remote fee
            # (would have gotten neg inbound but for remote fee)
            if (working_range_pct < NEGATIVE_INBOUND_TRIGGER and
                has_been_above_threshold and
                current_state.get('current_pct', 0) == 0 and
                inbound_fee == 0):
                # This likely means it was blocked by remote fee
                channels_remote_fee_too_high += 1

            # Update state
            updated_state[str(short_chan_id)] = {
                'inbound_fee': inbound_fee,
                'current_pct': inbound_pct,
                'working_range_pct': working_range_pct,
                'avg_fee': avg_fee,
                'has_been_above_threshold': has_been_above_threshold,
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

        logging.info(f"Updated INI: {channels_updated} channels processed, {channels_with_inbound} with inbound fees, "
                    f"{channels_never_above} never been above threshold, {channels_remote_fee_too_high} blocked by remote fee")
        print(f"Updated INI: {channels_updated} channels processed, {channels_with_inbound} with inbound fees, "
              f"{channels_never_above} never been above threshold, {channels_remote_fee_too_high} blocked by remote fee")

    except Exception as e:
        logging.error(f"Error updating INI with inbound fees: {str(e)}")
        print(f"Error updating INI with inbound fees: {str(e)}")

if __name__ == "__main__":
    update_ini_with_inbound()