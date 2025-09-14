#!/usr/bin/env python3
import json
import subprocess
from datetime import datetime
import logging
import os
import configparser

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_maxhtlc_wrapper.log'),
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s')

# Configuration constants
MAX_HTLC_RATIO = 0.98  # 98% of usable balance
RESERVE_OFFSET = 0.01  # 1% of capacity reserved and unusable
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')
CHAN_IDS = []  # Empty to process all channels
EXCLUDE_CHAN_IDS = []  # Add your channel IDs here

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

def scid_to_x_format(scid):
    """Convert decimal SCID to x format"""
    scid_int = int(scid)
    block_height = scid_int >> 40
    tx_index = (scid_int >> 16) & 0xFFFFFF
    output_index = scid_int & 0xFFFF
    return f"{block_height}x{tx_index}x{output_index}"

def get_current_max_htlc(short_chan_id, local_pubkey):
    """Get current max_htlc_msat from channel policy"""
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
            return None

        # max_htlc_msat might be a string in the JSON response
        max_htlc_str = policy.get('max_htlc_msat', '0')
        return int(max_htlc_str)
    except Exception as e:
        logging.error(f"Error getting max_htlc for {short_chan_id}: {str(e)}")
        return None

def update_max_htlc():
    """Update max HTLC for all channels to 98% of usable balance (after reserve)"""
    try:
        # Check if the INI file exists
        if not os.path.exists(CHARGE_INI_FILE):
            logging.error(f"INI file {CHARGE_INI_FILE} not found. Run autofee_wrapper.py first.")
            print(f"Error: INI file {CHARGE_INI_FILE} not found. Run autofee_wrapper.py first.")
            return

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

        channels_updated = 0
        total_channels = 0

        # Summary statistics for logging
        max_increase_pct = 0
        max_decrease_pct = 0
        max_increase_chan = None
        max_decrease_chan = None

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

            total_channels += 1

            # Calculate usable balance after accounting for channel reserve
            capacity = int(chan.get('capacity', 0))
            local_balance = int(chan.get('local_balance', 0))

            # Special case: 0 balance channels get 1 sat max HTLC
            if local_balance == 0:
                new_max_htlc_msat = 1000  # 1 sat in millisats
                reserve_amount = 0
                usable_balance = 0
            else:
                reserve_amount = int(capacity * RESERVE_OFFSET)
                usable_balance = max(0, local_balance - reserve_amount)
                new_max_htlc_msat = int(usable_balance * MAX_HTLC_RATIO * 1000)  # Convert sats to msats

            # Get current max_htlc_msat
            current_max_htlc_msat = get_current_max_htlc(short_chan_id, local_pubkey)

            # Find the section in the INI for this channel
            short_channel_id_x = scid_to_x_format(short_chan_id)
            section_name = f"autofee-{short_channel_id_x}"

            # Update the INI section
            if config.has_section(section_name):
                # Always set max_htlc_msat
                config.set(section_name, 'max_htlc_msat', str(int(new_max_htlc_msat)))
                channels_updated += 1

                # Calculate percentage change for logging
                if current_max_htlc_msat and current_max_htlc_msat > 0:
                    change_pct = ((new_max_htlc_msat - current_max_htlc_msat) / current_max_htlc_msat) * 100

                    # Track maximum changes
                    if change_pct > max_increase_pct:
                        max_increase_pct = change_pct
                        max_increase_chan = chan_id
                    if change_pct < max_decrease_pct:
                        max_decrease_pct = change_pct
                        max_decrease_chan = chan_id

                    # Convert to sats for more readable logging
                    current_sats = current_max_htlc_msat // 1000
                    new_sats = new_max_htlc_msat // 1000
                    local_balance_str = f"{local_balance:,}"
                    reserve_sats = reserve_amount
                    usable_sats = usable_balance
                    current_sats_str = f"{current_sats:,}"
                    new_sats_str = f"{new_sats:,}"

                    logging.info(f"Channel {chan_id}: capacity={capacity:,}, local_balance={local_balance_str} sats, "
                               f"{'0-balance channel, set to 1 sat' if local_balance == 0 else f'reserve={reserve_sats:,}, usable={usable_sats:,}'}, "
                               f"max_htlc: {current_sats_str} -> {new_sats_str} sats "
                               f"({change_pct:+.1f}%)")
                else:
                    # No previous value or zero value
                    new_sats = new_max_htlc_msat // 1000
                    local_balance_str = f"{local_balance:,}"
                    reserve_sats = reserve_amount
                    usable_sats = usable_balance
                    new_sats_str = f"{new_sats:,}"
                    logging.info(f"Channel {chan_id}: capacity={capacity:,}, local_balance={local_balance_str} sats, "
                               f"{'0-balance channel, set to 1 sat' if local_balance == 0 else f'reserve={reserve_sats:,}, usable={usable_sats:,}'}, "
                               f"max_htlc set to {new_sats_str} sats (no previous value)")
            else:
                # Channel has no section (shouldn't happen if autofee_wrapper.py ran)
                logging.warning(f"Channel {chan_id} has no section in INI, skipping")

        # Write updated INI file with atomic write
        temp_file = CHARGE_INI_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            config.write(f)
        os.replace(temp_file, CHARGE_INI_FILE)

        # Log summary
        logging.info(f"=== Max HTLC Update Summary ===")
        logging.info(f"Reserve offset: {RESERVE_OFFSET*100}% of capacity")
        logging.info(f"Total channels processed: {total_channels}")
        logging.info(f"Channels updated: {channels_updated}")

        if max_increase_chan:
            logging.info(f"Largest increase: Channel {max_increase_chan} (+{max_increase_pct:.1f}%)")
        if max_decrease_chan:
            logging.info(f"Largest decrease: Channel {max_decrease_chan} ({max_decrease_pct:.1f}%)")

        logging.info(f"=== End Summary ===")

        print(f"Updated max HTLC for {channels_updated} channels (with {RESERVE_OFFSET*100}% reserve offset)")

    except Exception as e:
        logging.error(f"Error updating max HTLC: {str(e)}")
        print(f"Error updating max HTLC: {str(e)}")

if __name__ == "__main__":
    update_max_htlc()