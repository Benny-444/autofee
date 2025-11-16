#!/usr/bin/env python3
"""
Autofee Group Wrapper - Simple channel fee synchronization

This script runs as the final step in the autofee pipeline to synchronize fees
across grouped channels. It reads the fees already calculated by previous wrappers
from dynamic_charge.ini and applies a group strategy.

Features:
1. Group multiple channels together (e.g., multiple channels to same node)
2. Apply the same fee to all channels in a group
3. Choose fee strategy: highest, lowest, average, or static
4. Process multiple groups with different configurations
5. Optionally synchronize inbound fees with same or different strategy

Configuration example:
CHANNEL_GROUPS = [
    {
        'name': 'NodeA_channels',
        'chan_ids': ['chan_id_1', 'chan_id_2'],  # Channels to same node
        'strategy': 'highest',  # Strategy for outbound fees
        'sync_inbound': True,  # Also synchronize inbound fees
        'inbound_strategy': 'lowest',  # Optional: different strategy for inbound
        'static_fee': 100,  # Only used if strategy is 'static'
        'static_inbound_fee': -50,  # Only used if inbound_strategy is 'static'
        'enabled': True
    }
]
"""
import logging
import os
import configparser
from typing import Dict, List

# Ensure directory exists
os.makedirs(os.path.expanduser('~/autofee'), exist_ok=True)

logging.basicConfig(filename=os.path.expanduser('~/autofee/autofee_group_wrapper.log'), 
                    level=logging.INFO, 
                    format='%(asctime)s %(levelname)s: %(message)s')

# ============================================================================
# CONFIGURATION - MODIFY THESE SETTINGS
# ============================================================================

# Channel group configuration
# Each group can have multiple channels that will share the same fee
CHANNEL_GROUPS = [
    # Example group 1: Two channels to same node, sync both outbound and inbound
    # {
    #     'name': 'ACINQ_channels',
    #     'chan_ids': ['996507179527241729', '996507179527241730'],  # Add your channel IDs (SCID format)
    #     'strategy': 'highest',  # Strategy for outbound fees
    #     'sync_inbound': True,  # Also synchronize inbound fees
    #     'inbound_strategy': 'lowest',  # Optional: different strategy for inbound (defaults to 'strategy' if not set)
    #     'enabled': True
    # },
    
    # Example group 2: Channels with static fees for both outbound and inbound
    # {
    #     'name': 'LowFee_group',
    #     'chan_ids': ['996507179527241731', '996507179527241732'],
    #     'strategy': 'static',
    #     'static_fee': 50,  # Fixed 50 ppm for outbound
    #     'sync_inbound': True,
    #     'inbound_strategy': 'static',
    #     'static_inbound_fee': -25,  # Fixed -25 ppm for inbound
    #     'enabled': True
    # },
    
    # Example group 3: Only sync outbound (original behavior)
    # {
    #     'name': 'Outbound_only_group',
    #     'chan_ids': ['996507179527241733', '996507179527241734'],
    #     'strategy': 'average',
    #     'sync_inbound': False,  # Don't touch inbound fees
    #     'enabled': True
    # },
    
    # Example group 4: Sync both with same strategy
    # {
    #     'name': 'Same_strategy_group',
    #     'chan_ids': ['996507179527241735', '996507179527241736'],
    #     'strategy': 'highest',  # Use highest for both outbound and inbound
    #     'sync_inbound': True,
    #     # No inbound_strategy specified, so uses 'highest' for both
    #     'enabled': True
    # },
]

# File path
CHARGE_INI_FILE = os.path.expanduser('~/autofee/dynamic_charge.ini')

# ============================================================================
# FUNCTIONS
# ============================================================================

def scid_to_x_format(scid: str) -> str:
    """Convert decimal SCID to x format for INI sections"""
    try:
        scid_int = int(scid)
        block_height = scid_int >> 40
        tx_index = (scid_int >> 16) & 0xFFFFFF
        output_index = scid_int & 0xFFFF
        return f"{block_height}x{tx_index}x{output_index}"
    except:
        return None

def get_channel_fees_from_ini(chan_ids: List[str], config: configparser.ConfigParser, fee_type: str = 'outbound') -> Dict[str, int]:
    """
    Read current fees for specified channels from the INI file.
    
    Args:
        chan_ids: List of channel IDs to look for
        config: ConfigParser object with INI data
        fee_type: 'outbound' for fee_ppm, 'inbound' for inbound_fee_ppm
    
    Returns:
        dict of {scid: fee_value}
    """
    channel_fees = {}
    fee_field = 'fee_ppm' if fee_type == 'outbound' else 'inbound_fee_ppm'
    
    for section in config.sections():
        # Check if this section has a chan.id
        if config.has_option(section, 'chan.id'):
            scid = config.get(section, 'chan.id')
            
            # Check if this channel is in our list
            if scid in chan_ids:
                if config.has_option(section, fee_field):
                    try:
                        fee = int(config.get(section, fee_field))
                        channel_fees[scid] = fee
                        logging.info(f"  Found channel {scid} with {fee_type} fee {fee} ppm")
                    except ValueError:
                        logging.warning(f"  Invalid {fee_type} fee value for channel {scid}")
                elif fee_type == 'inbound':
                    # If no inbound fee is set, treat as 0
                    channel_fees[scid] = 0
                    logging.info(f"  Found channel {scid} with no inbound fee (treating as 0)")
    
    return channel_fees

def determine_group_fee(channel_fees: Dict[str, int], strategy: str, static_fee: int = None, fee_type: str = 'outbound') -> int:
    """
    Determine the fee to apply based on strategy.
    
    Args:
        channel_fees: Dict of {scid: current_fee}
        strategy: 'highest', 'lowest', 'average', or 'static'
        static_fee: Fee to use if strategy is 'static'
        fee_type: Type of fee being processed (for logging)
    
    Returns:
        The fee to apply to all channels
    """
    if strategy == 'static':
        # Use configured static fee
        final_fee = static_fee if static_fee is not None else 100
        logging.info(f"  Using static {fee_type} fee: {final_fee} ppm")
        
    elif strategy == 'highest':
        # Use the highest fee from the group
        final_fee = max(channel_fees.values())
        logging.info(f"  Using highest {fee_type} fee: {final_fee} ppm")
        
    elif strategy == 'lowest':
        # Use the lowest fee from the group
        final_fee = min(channel_fees.values())
        logging.info(f"  Using lowest {fee_type} fee: {final_fee} ppm")
        
    elif strategy == 'average':
        # Use the average of all fees
        final_fee = round(sum(channel_fees.values()) / len(channel_fees))
        logging.info(f"  Using average {fee_type} fee: {final_fee} ppm")
        
    else:
        logging.error(f"  Unknown strategy '{strategy}' for {fee_type}, using average")
        final_fee = round(sum(channel_fees.values()) / len(channel_fees))
    
    return final_fee

def process_channel_group(group: dict, config: configparser.ConfigParser) -> Dict[str, dict]:
    """
    Process a group of channels and determine fees to apply.
    
    Returns a dict of {scid: {'outbound': fee, 'inbound': fee}}
    """
    group_name = group.get('name', 'unnamed')
    chan_ids = group.get('chan_ids', [])
    strategy = group.get('strategy', 'average')
    sync_inbound = group.get('sync_inbound', False)
    inbound_strategy = group.get('inbound_strategy', strategy)  # Default to same as outbound
    
    if not chan_ids:
        logging.warning(f"Group '{group_name}' has no channel IDs")
        return {}
    
    logging.info(f"Processing group '{group_name}'")
    logging.info(f"  Outbound strategy: {strategy}")
    if sync_inbound:
        logging.info(f"  Inbound strategy: {inbound_strategy}")
    else:
        logging.info(f"  Inbound sync: disabled")
    
    # Get current outbound fees for channels in this group
    outbound_fees = get_channel_fees_from_ini(chan_ids, config, 'outbound')
    
    if not outbound_fees:
        logging.warning(f"No channels found in INI for group '{group_name}'")
        return {}
    
    logging.info(f"  Found {len(outbound_fees)} channels in group")
    
    # Determine outbound fee to apply
    final_outbound = determine_group_fee(
        outbound_fees, 
        strategy, 
        group.get('static_fee'),
        'outbound'
    )
    
    # Build result with outbound fees
    result = {}
    for scid in outbound_fees.keys():
        result[scid] = {'outbound': final_outbound}
        if outbound_fees[scid] != final_outbound:
            logging.info(f"  Channel {scid} outbound: {outbound_fees[scid]} -> {final_outbound} ppm")
        else:
            logging.info(f"  Channel {scid} outbound: already at {final_outbound} ppm")
    
    # Process inbound fees if enabled
    if sync_inbound:
        inbound_fees = get_channel_fees_from_ini(chan_ids, config, 'inbound')
        
        if inbound_fees:
            # Determine inbound fee to apply
            final_inbound = determine_group_fee(
                inbound_fees,
                inbound_strategy,
                group.get('static_inbound_fee'),
                'inbound'
            )
            
            # Add inbound fees to result
            for scid in inbound_fees.keys():
                if scid in result:
                    result[scid]['inbound'] = final_inbound
                    if inbound_fees[scid] != final_inbound:
                        logging.info(f"  Channel {scid} inbound: {inbound_fees[scid]} -> {final_inbound} ppm")
                    else:
                        logging.info(f"  Channel {scid} inbound: already at {final_inbound} ppm")
    
    return result

def update_group_channels():
    """Main function to synchronize fees for channel groups"""
    try:
        # Check if INI file exists
        if not os.path.exists(CHARGE_INI_FILE):
            logging.error(f"dynamic_charge.ini not found. Run autofee scripts first.")
            print(f"Error: dynamic_charge.ini not found. Run autofee scripts first.")
            return

        # Check if any groups are configured
        enabled_groups = [g for g in CHANNEL_GROUPS if g.get('enabled', False)]
        if not enabled_groups:
            logging.info("No channel groups enabled. Configure CHANNEL_GROUPS to use this script.")
            print("No channel groups enabled. Configure CHANNEL_GROUPS in the script to use it.")
            return

        # Parse existing INI file
        config = configparser.ConfigParser()
        config.read(CHARGE_INI_FILE)

        logging.info(f"Starting group fee synchronization for {len(enabled_groups)} groups")

        # Process each enabled group
        all_updates = {}
        for group in enabled_groups:
            group_updates = process_channel_group(group, config)
            # Merge updates (later groups can override earlier ones)
            for scid, fees in group_updates.items():
                if scid in all_updates:
                    all_updates[scid].update(fees)
                else:
                    all_updates[scid] = fees

        if not all_updates:
            logging.warning("No channels were updated in any group")
            print("Warning: No channels were updated. Check your group configuration.")
            return

        # Apply updates to the INI file
        channels_updated = 0
        inbound_updated = 0
        
        for scid, fees in all_updates.items():
            # Find the section for this channel
            short_channel_id_x = scid_to_x_format(scid)
            section_name = f"autofee-{short_channel_id_x}"

            if config.has_section(section_name):
                # Update outbound fee
                if 'outbound' in fees:
                    config.set(section_name, 'fee_ppm', str(int(fees['outbound'])))
                    channels_updated += 1
                
                # Update inbound fee if present
                if 'inbound' in fees:
                    config.set(section_name, 'inbound_fee_ppm', str(int(fees['inbound'])))
                    inbound_updated += 1
            else:
                logging.warning(f"Section {section_name} not found for channel {scid}")

        # Write updated INI file with atomic write
        temp_file = CHARGE_INI_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            config.write(f)
        os.replace(temp_file, CHARGE_INI_FILE)

        log_msg = f"Successfully updated {channels_updated} outbound fees"
        if inbound_updated > 0:
            log_msg += f" and {inbound_updated} inbound fees"
        log_msg += f" across {len(enabled_groups)} groups"
        
        logging.info(log_msg)
        print(log_msg)

    except Exception as e:
        logging.error(f"Error updating group channels: {str(e)}")
        print(f"Error updating group channels: {str(e)}")

if __name__ == "__main__":
    update_group_channels()