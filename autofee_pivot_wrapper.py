#!/usr/bin/env python3
"""
Autofee Pivot Wrapper - Custom fee management with adjustable pivot point

This script allows targeting specific channels with a custom liquidity pivot point
where the average fee is centered. The fee curve has two linear segments meeting
at the pivot point, with slopes determined by the pivot position.

Two segments:
1. From pivot to 100% liquidity: Linear from avg_fee to 0
2. From 0% to pivot liquidity: Linear continuation with same rate of change

Examples with avg_fee=1000 ppm:

Pivot=0.5 (50%, standard behavior):
  - At 100% liquidity: 0 ppm
  - At 50% liquidity: 1000 ppm (avg_fee)
  - At 0% liquidity: 2000 ppm

Pivot=0.6 (60% - protects against low liquidity more aggressively):
  - At 100% liquidity: 0 ppm
  - At 60% liquidity: 1000 ppm (avg_fee)
  - At 40% liquidity: 1500 ppm
  - At 20% liquidity: 2000 ppm (2*avg_fee reached here)
  - At 0% liquidity: 2500 ppm (exceeds 2*avg_fee!)

Pivot=0.4 (40% - encourages outflow even at moderate liquidity):
  - At 100% liquidity: 0 ppm (clamped - would be negative)
  - At 80% liquidity: 0 ppm (naturally reaches 0 here)
  - At 40% liquidity: 1000 ppm (avg_fee)
  - At 20% liquidity: 1500 ppm
  - At 0% liquidity: 2000 ppm

The higher the pivot, the steeper the fee increase below the pivot point.
The lower the pivot, the more gradual the fee increase below the pivot.
"""
import json
import subprocess
import logging
import os
import configparser

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_pivot_wrapper.log'),
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s')

# Configuration constants
AVG_FEE_PIVOT = 0.5       # Pivot point for avg fee (0.5 = 50%, 0.6 = 60%, etc.)
ADJUSTMENT_FACTOR = 0.05  # Percentage of difference to apply as adjustment (0.05 = 5%)
AVG_FEE_FILE = os.path.expanduser('~/autofee/avg_fees.json')
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')
STAGNANT_STATE_FILE = os.path.expanduser('~/autofee/stagnant_state.json')
CHAN_IDS = []  # REQUIRED: Add your specific channel ID(s) here
EXCLUDE_CHAN_IDS = []  # Not needed since we're targeting specific channels

def load_avg_fees():
    """Load average fees from the main autofee script's JSON file"""
    try:
        if os.path.exists(AVG_FEE_FILE):
            with open(AVG_FEE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading avg_fees: {str(e)}")
    return {}

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
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"lncli command failed: {args}, error: {e.output.decode()}")
        raise
    except Exception as e:
        logging.error(f"Error running lncli {args}: {str(e)}")
        raise

def get_channel_info(short_chan_id, local_pubkey):
    """Get channel info and extract local policy"""
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
            return {'current_fee_ppm': 0}

        return {
            'current_fee_ppm': int(policy.get('fee_rate_milli_msat', 0))
        }
    except Exception as e:
        logging.error(f"Error getting channel info for {short_chan_id}: {str(e)}")
        return {'current_fee_ppm': 0}

def scid_to_x_format(scid):
    """Convert decimal SCID to x format"""
    scid_int = int(scid)
    block_height = scid_int >> 40
    tx_index = (scid_int >> 16) & 0xFFFFFF
    output_index = scid_int & 0xFFFF
    return f"{block_height}x{tx_index}x{output_index}"

def update_pivot_channels():
    """Update fees for specific channels with custom pivot point"""
    try:
        # Check configuration
        if not CHAN_IDS:
            logging.error("No channel IDs specified in CHAN_IDS. Please configure the script.")
            print("Error: No channel IDs specified in CHAN_IDS. Please configure the script.")
            return

        # Check if required files exist
        if not os.path.exists(AVG_FEE_FILE):
            logging.error(f"avg_fees.json not found. Run autofee_wrapper.py first.")
            print(f"Error: avg_fees.json not found. Run autofee_wrapper.py first.")
            return

        if not os.path.exists(CHARGE_INI_FILE):
            logging.error(f"dynamic_charge.ini not found. Run autofee_wrapper.py first.")
            print(f"Error: dynamic_charge.ini not found. Run autofee_wrapper.py first.")
            return

        # Load average fees
        avg_fees = load_avg_fees()
        if not avg_fees:
            logging.error("No average fees data available")
            return

        # Load stagnant state
        stagnant_state = load_stagnant_state()

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
        channels_processed = 0

        logging.info(f"Processing channels with pivot point at {AVG_FEE_PIVOT*100:.0f}% liquidity")

        for chan in channels:
            chan_id = chan.get('chan_id')
            short_chan_id = chan.get('scid')

            # Only process channels in CHAN_IDS
            if chan_id not in CHAN_IDS and str(short_chan_id) not in CHAN_IDS:
                continue

            channels_processed += 1

            # Skip inactive channels
            if not chan.get('active', False):
                logging.info(f"Skipping inactive channel {chan_id}")
                continue

            # Check if channel is stagnant
            stagnant_info = stagnant_state.get(str(short_chan_id), {})
            is_stagnant = stagnant_info.get('is_stagnant', False)

            if is_stagnant:
                # Skip stagnant channels - let stagnant wrapper handle them
                logging.info(f"Channel {chan_id}: Skipping stagnant channel (will be handled by stagnant wrapper)")
                continue

            # Get avg_fee for this channel
            avg_fee = avg_fees.get(str(short_chan_id))
            if avg_fee is None:
                logging.warning(f"No avg_fee found for channel {chan_id}, skipping")
                continue

            # Get current fee from node
            channel_info = get_channel_info(short_chan_id, local_pubkey)
            current_fee = channel_info['current_fee_ppm']

            # Calculate liquidity ratio
            capacity = float(chan.get('capacity', 1))
            if capacity > 0:
                ratio = float(chan.get('local_balance', 0)) / capacity
            else:
                ratio = 0.5

            # Calculate target fee with custom pivot point
            # The fee should be avg_fee at the pivot point and scale linearly
            #
            # For pivot >= 0.5: Fee reaches 0 at 100% liquidity
            # For pivot < 0.5: Fee reaches 0 at 2*pivot liquidity

            if AVG_FEE_PIVOT >= 0.5:
                # Normal case: fee goes from avg_fee at pivot to 0 at 100%
                if ratio >= AVG_FEE_PIVOT:
                    # Above pivot: linear to 0 at 100%
                    set_fee = avg_fee * (1 - ratio) / (1 - AVG_FEE_PIVOT)
                else:
                    # Below pivot: continue with same slope
                    set_fee = avg_fee * (1 + (AVG_FEE_PIVOT - ratio) / (1 - AVG_FEE_PIVOT))
            else:
                # Special case: fee reaches 0 before 100%
                zero_point = 2 * AVG_FEE_PIVOT  # Where fee reaches 0
                if ratio >= zero_point:
                    # Above zero point: fee is 0
                    set_fee = 0
                elif ratio >= AVG_FEE_PIVOT:
                    # Between pivot and zero point: linear to 0
                    set_fee = avg_fee * (zero_point - ratio) / (zero_point - AVG_FEE_PIVOT)
                else:
                    # Below pivot: linear from higher fee to avg_fee
                    set_fee = avg_fee * (1 + (AVG_FEE_PIVOT - ratio) / AVG_FEE_PIVOT)

            set_fee = max(0, round(set_fee))  # Ensure non-negative

            # Calculate adjustment with minimum ±1 ppm movement
            adjustment = ADJUSTMENT_FACTOR * (set_fee - current_fee)
            if adjustment != 0:
                adjustment = max(1, abs(round(adjustment))) * (1 if adjustment > 0 else -1)
            new_fee = current_fee + adjustment
            new_fee = max(0, new_fee)  # Ensure non-negative

            # Find or create the section in the INI
            short_channel_id_x = scid_to_x_format(short_chan_id)
            section_name = f"autofee-{short_channel_id_x}"

            if not config.has_section(section_name):
                config.add_section(section_name)

            # Update the section
            config.set(section_name, 'chan.id', str(short_chan_id))
            config.set(section_name, 'strategy', 'static')
            config.set(section_name, 'fee_ppm', str(int(new_fee)))

            # Preserve other settings if they exist
            if config.has_option(section_name, 'inbound_fee_ppm'):
                # Keep existing inbound fee
                pass
            if config.has_option(section_name, 'max_htlc_msat'):
                # Keep existing max_htlc_msat
                pass

            channels_updated += 1

            logging.info(f"Channel {chan_id}: pivot={AVG_FEE_PIVOT:.2f}, avg_fee={avg_fee}, "
                        f"ratio={ratio:.2f}, current={current_fee}, target={set_fee}, new={new_fee}")

        # Write updated INI file with atomic write
        temp_file = CHARGE_INI_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            config.write(f)
        os.replace(temp_file, CHARGE_INI_FILE)

        if channels_processed == 0:
            logging.warning(f"No channels found matching CHAN_IDS: {CHAN_IDS}")
            print(f"Warning: No channels found matching CHAN_IDS: {CHAN_IDS}")
        else:
            logging.info(f"Updated {channels_updated} channels with pivot at {AVG_FEE_PIVOT*100:.0f}%")
            print(f"Updated {channels_updated} channels with pivot at {AVG_FEE_PIVOT*100:.0f}%")

    except Exception as e:
        logging.error(f"Error updating pivot channels: {str(e)}")
        print(f"Error updating pivot channels: {str(e)}")

if __name__ == "__main__":
    update_pivot_channels()