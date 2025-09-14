#!/usr/bin/env python3
"""
Autofee Minimum Fee Wrapper - Enforce minimum fees for specified channels

This script runs after the main autofee wrappers to ensure certain channels
maintain a minimum fee level. It can use either a static minimum or the 
channel's average fee (from avg_fees.json) as the minimum.

Configuration:
- Each channel can have either a static minimum fee or use its avg_fee
- The script only raises fees if they're below the minimum (never lowers)
- Preserves all other settings (inbound fees, max_htlc, etc.)
"""
import json
import logging
import os
import configparser
from typing import Dict, Optional

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_minfee_wrapper.log'), 
                    level=logging.INFO, 
                    format='%(asctime)s %(levelname)s: %(message)s')

# ============================================================================
# CONFIGURATION - MODIFY THESE SETTINGS
# ============================================================================

# Channel minimum fee configuration
# Each channel can have either a static minimum or use its avg_fee
CHANNEL_MINIMUMS = [
    # Example 1: Static minimum fee
    # {
    #     'chan_id': '996507179527241729',  # SCID format
    #     'min_type': 'static',  # Use a fixed minimum
    #     'min_value': 100,  # Minimum 100 ppm
    #     'enabled': True
    # },
    
    # Example 2: Use average fee as minimum
    # {
    #     'chan_id': '996507179527241730',  # SCID format
    #     'min_type': 'avg_fee',  # Use the channel's avg_fee as minimum
    #     'enabled': True
    # },
    
    # Example 3: Another static minimum
    # {
    #     'chan_id': '996507179527241731',
    #     'min_type': 'static',
    #     'min_value': 50,  # Lower minimum for this channel
    #     'enabled': True
    # },
]

# File paths
AVG_FEE_FILE = os.path.expanduser('~/autofee/avg_fees.json')
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')

# ============================================================================
# FUNCTIONS
# ============================================================================

def load_avg_fees() -> Dict[str, float]:
    """Load average fees from JSON file"""
    try:
        if os.path.exists(AVG_FEE_FILE):
            with open(AVG_FEE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading avg_fees.json: {str(e)}")
    return {}

def scid_to_x_format(scid: str) -> Optional[str]:
    """Convert decimal SCID to x format for INI sections"""
    try:
        scid_int = int(scid)
        block_height = scid_int >> 40
        tx_index = (scid_int >> 16) & 0xFFFFFF
        output_index = scid_int & 0xFFFF
        return f"{block_height}x{tx_index}x{output_index}"
    except Exception as e:
        logging.error(f"Error converting SCID {scid}: {str(e)}")
        return None

def get_channel_minimum(channel_config: dict, avg_fees: Dict[str, float]) -> Optional[int]:
    """
    Determine the minimum fee for a channel based on its configuration.
    
    Args:
        channel_config: Configuration dict for the channel
        avg_fees: Dictionary of average fees by SCID
    
    Returns:
        The minimum fee in ppm, or None if it cannot be determined
    """
    chan_id = channel_config.get('chan_id')
    min_type = channel_config.get('min_type', 'static')
    
    if min_type == 'static':
        # Use the configured static value
        min_value = channel_config.get('min_value')
        if min_value is None:
            logging.warning(f"Channel {chan_id} has static min_type but no min_value")
            return None
        return int(min_value)
    
    elif min_type == 'avg_fee':
        # Use the channel's average fee
        avg_fee = avg_fees.get(str(chan_id))
        if avg_fee is None:
            logging.warning(f"Channel {chan_id} has avg_fee min_type but no avg_fee found")
            return None
        return int(round(avg_fee))
    
    else:
        logging.warning(f"Channel {chan_id} has unknown min_type: {min_type}")
        return None

def enforce_minimum_fees():
    """Main function to enforce minimum fees for configured channels"""
    try:
        # Check if required files exist
        if not os.path.exists(CHARGE_INI_FILE):
            logging.error(f"dynamic_charge.ini not found. Run autofee scripts first.")
            print(f"Error: dynamic_charge.ini not found. Run autofee scripts first.")
            return
        
        if not os.path.exists(AVG_FEE_FILE):
            logging.error(f"avg_fees.json not found. Run autofee_wrapper.py first.")
            print(f"Error: avg_fees.json not found. Run autofee_wrapper.py first.")
            return
        
        # Get enabled channels from configuration
        enabled_channels = [c for c in CHANNEL_MINIMUMS if c.get('enabled', False)]
        
        if not enabled_channels:
            logging.info("No channels configured for minimum fee enforcement.")
            print("No channels configured for minimum fee enforcement.")
            return
        
        logging.info(f"Starting minimum fee enforcement for {len(enabled_channels)} channels")
        
        # Load average fees
        avg_fees = load_avg_fees()
        
        # Parse existing INI file
        config = configparser.ConfigParser()
        config.read(CHARGE_INI_FILE)
        
        channels_checked = 0
        channels_raised = 0
        channels_already_ok = 0
        channels_not_found = 0
        
        # Process each configured channel
        for channel_config in enabled_channels:
            chan_id = channel_config.get('chan_id')
            if not chan_id:
                logging.warning("Channel configuration missing chan_id, skipping")
                continue
            
            channels_checked += 1
            
            # Determine the minimum fee for this channel
            min_fee = get_channel_minimum(channel_config, avg_fees)
            if min_fee is None:
                logging.warning(f"Could not determine minimum fee for channel {chan_id}")
                continue
            
            # Find the section for this channel in the INI
            short_channel_id_x = scid_to_x_format(chan_id)
            if not short_channel_id_x:
                logging.warning(f"Could not convert SCID {chan_id} to x format")
                continue
            
            section_name = f"autofee-{short_channel_id_x}"
            
            if not config.has_section(section_name):
                logging.warning(f"Section {section_name} not found for channel {chan_id}")
                channels_not_found += 1
                continue
            
            # Get current fee
            if not config.has_option(section_name, 'fee_ppm'):
                logging.warning(f"No fee_ppm found for channel {chan_id}")
                continue
            
            try:
                current_fee = int(config.get(section_name, 'fee_ppm'))
            except ValueError:
                logging.error(f"Invalid fee_ppm value for channel {chan_id}")
                continue
            
            # Check if fee needs to be raised
            if current_fee < min_fee:
                # Raise the fee to minimum
                config.set(section_name, 'fee_ppm', str(min_fee))
                channels_raised += 1
                
                min_type = channel_config.get('min_type')
                min_source = f"avg_fee ({avg_fees.get(str(chan_id), 0):.0f})" if min_type == 'avg_fee' else f"static ({min_fee})"
                logging.info(f"Channel {chan_id}: Raised fee from {current_fee} to {min_fee} ppm (minimum: {min_source})")
            else:
                channels_already_ok += 1
                logging.info(f"Channel {chan_id}: Fee {current_fee} ppm already >= minimum {min_fee} ppm")
        
        # Write updated INI file if any changes were made
        if channels_raised > 0:
            # Atomic write
            temp_file = CHARGE_INI_FILE + '.tmp'
            with open(temp_file, 'w') as f:
                config.write(f)
            os.replace(temp_file, CHARGE_INI_FILE)
            
            logging.info(f"Updated INI file with {channels_raised} fee increases")
        
        # Summary
        summary = f"Minimum fee enforcement complete: {channels_checked} checked, {channels_raised} raised, {channels_already_ok} already ok"
        if channels_not_found > 0:
            summary += f", {channels_not_found} not found"
        
        logging.info(summary)
        print(summary)
        
    except Exception as e:
        logging.error(f"Error enforcing minimum fees: {str(e)}")
        print(f"Error enforcing minimum fees: {str(e)}")

if __name__ == "__main__":
    enforce_minimum_fees()