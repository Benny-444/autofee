#!/usr/bin/env python3
import json
import subprocess
import sys

def run_lncli(args):
    """Execute lncli command and parse JSON output"""
    try:
        output = subprocess.check_output(['lncli'] + args, stderr=subprocess.STDOUT)
        return json.loads(output.decode())
    except Exception as e:
        print(f"Error running lncli {args}: {str(e)}")
        sys.exit(1)

def safe_int(value, default=0):
    """Safely convert value to int"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def get_channel_policy(chan_point, short_chan_id, local_pubkey):
    """Get current channel policy for our side"""
    try:
        # Get channel info
        chan_info = run_lncli(['getchaninfo', short_chan_id])

        # Determine which policy is ours
        if chan_info.get('node1_pub') == local_pubkey:
            policy = chan_info.get('node1_policy', {})
        elif chan_info.get('node2_pub') == local_pubkey:
            policy = chan_info.get('node2_policy', {})
        else:
            return None

        return {
            'base_fee_msat': safe_int(policy.get('fee_base_msat'), 1000),
            'fee_rate_ppm': safe_int(policy.get('fee_rate_milli_msat'), 1),
            'time_lock_delta': safe_int(policy.get('time_lock_delta'), 40),
            'min_htlc_msat': safe_int(policy.get('min_htlc_msat'), 1000),  # FIXED: was 'min_htlc'
            'max_htlc_msat': safe_int(policy.get('max_htlc_msat'), 0)
        }
    except Exception as e:
        return None

def reset_max_htlc(dry_run=False):
    """Reset all channels to 99% max HTLC while preserving other settings"""
    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")

    # Get local node info
    local_info = run_lncli(['getinfo'])
    local_pubkey = local_info.get('identity_pubkey')
    if not local_pubkey:
        print("Error: Could not get local pubkey")
        sys.exit(1)

    # Get all channels
    channels = run_lncli(['listchannels'])['channels']

    success_count = 0
    error_count = 0
    skip_count = 0

    print(f"Processing {len(channels)} channels...\n")

    for chan in channels:
        chan_point = chan.get('channel_point')
        short_chan_id = chan.get('scid')
        capacity = safe_int(chan.get('capacity'))

        # Skip if missing required fields
        if not chan_point or not short_chan_id:
            skip_count += 1
            print(f"⚠ Skipping channel {chan_point or 'unknown'}: Missing required fields")
            continue

        # Skip if no capacity
        if capacity <= 0:
            skip_count += 1
            print(f"⚠ Skipping channel {short_chan_id}: Zero or invalid capacity")
            continue

        # Get current policy
        current_policy = get_channel_policy(chan_point, short_chan_id, local_pubkey)
        if not current_policy:
            error_count += 1
            print(f"✗ Channel {short_chan_id}: Could not retrieve current policy")
            continue

        # Calculate 99% of capacity in millisats
        new_max_htlc_msat = int(capacity * 0.99 * 1000)

        # Show what would be done
        old_max = current_policy['max_htlc_msat'] // 1000
        new_max = new_max_htlc_msat // 1000

        if dry_run:
            print(f"[DRY RUN] Channel {short_chan_id}: Would reset max_htlc from {old_max:,} to {new_max:,} sats")
            print(f"          Current settings: base_fee={current_policy['base_fee_msat']}msat, "
                  f"fee_rate={current_policy['fee_rate_ppm']}ppm, "
                  f"time_lock_delta={current_policy['time_lock_delta']}, "
                  f"min_htlc={current_policy['min_htlc_msat']}msat")
            success_count += 1
            continue

        try:
            # Update channel policy with all existing values + new max_htlc
            run_lncli([
                'updatechanpolicy',
                '--chan_point', chan_point,
                '--base_fee_msat', str(current_policy['base_fee_msat']),
                '--fee_rate_ppm', str(current_policy['fee_rate_ppm']),
                '--time_lock_delta', str(current_policy['time_lock_delta']),
                '--min_htlc_msat', str(current_policy['min_htlc_msat']),
                '--max_htlc_msat', str(new_max_htlc_msat)
            ])

            success_count += 1
            print(f"✓ Channel {short_chan_id}: Reset max_htlc from {old_max:,} to {new_max:,} sats ({capacity:,} sat capacity)")

        except Exception as e:
            error_count += 1
            print(f"✗ Channel {short_chan_id}: Failed to update - {str(e)}")

    print(f"\n{'DRY RUN ' if dry_run else ''}Complete: {success_count} channels {'would be' if dry_run else ''} reset, "
          f"{error_count} errors, {skip_count} skipped")
    print(f"Total channels processed: {len(channels)}")

if __name__ == "__main__":
    # Check for dry-run flag
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    reset_max_htlc(dry_run)
