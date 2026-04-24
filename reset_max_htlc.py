#!/usr/bin/env python3
import json
import subprocess
import sys

# ==================================================
# Configuration
# ==================================================

# Channels to exclude from reset. List of decimal SCIDs as strings.
# Example: EXCLUDE_CHAN_IDS = ['1033572816056221697', '991957400425594881']
EXCLUDE_CHAN_IDS = []

# Fraction of capacity to use for max_htlc (LND default on channel open
# is approximately capacity - reserve, which is ~99% for typical channel sizes)
MAX_HTLC_RATIO = 0.99

# ==================================================


def run_lncli(args, exit_on_error=False):
    """Execute lncli command and parse JSON output.

    By default raises on error so callers can handle per-channel failures.
    Set exit_on_error=True for unrecoverable setup calls (getinfo, listchannels).
    """
    try:
        output = subprocess.check_output(['lncli'] + args, stderr=subprocess.STDOUT)
        # Some lncli commands return empty output on success (e.g. updatechanpolicy)
        decoded = output.decode().strip()
        if not decoded:
            return {}
        return json.loads(decoded)
    except subprocess.CalledProcessError as e:
        error_msg = e.output.decode().strip() if e.output else str(e)
        if exit_on_error:
            print(f"Fatal: lncli {' '.join(args)} failed: {error_msg}")
            sys.exit(1)
        raise RuntimeError(error_msg)
    except json.JSONDecodeError as e:
        if exit_on_error:
            print(f"Fatal: could not parse lncli output: {str(e)}")
            sys.exit(1)
        raise RuntimeError(f"JSON parse error: {str(e)}")


def safe_int(value, default=0):
    """Safely convert value to int"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def get_channel_policy(short_chan_id, local_pubkey):
    """Get current channel policy for our side.

    Returns dict on success, None on failure (with reason printed).
    Preserves ALL relevant fields - especially inbound fees, which LND
    will silently reset to 0 if not explicitly specified in updatechanpolicy
    (see lnd issue #8614).
    """
    try:
        chan_info = run_lncli(['getchaninfo', str(short_chan_id)])
    except Exception as e:
        print(f"  ✗ Channel {short_chan_id}: getchaninfo failed - {str(e)}")
        return None

    # Determine which policy is ours
    node1_pub = chan_info.get('node1_pub')
    node2_pub = chan_info.get('node2_pub')

    if node1_pub == local_pubkey:
        policy = chan_info.get('node1_policy')
    elif node2_pub == local_pubkey:
        policy = chan_info.get('node2_policy')
    else:
        print(f"  ✗ Channel {short_chan_id}: local pubkey matches neither node")
        return None

    if not policy:
        print(f"  ✗ Channel {short_chan_id}: our policy is empty (channel may still be gossiping)")
        return None

    # Note on LND's JSON field naming quirks:
    # - 'min_htlc' is the correct field name (NOT 'min_htlc_msat')
    # - 'fee_rate_milli_msat' actually holds PPM, not msat (legacy misnomer)
    # - 'inbound_fee_rate_milli_msat' likewise holds PPM
    # - Inbound fee fields can be absent on older LND versions; default 0 is safe
    return {
        'base_fee_msat': safe_int(policy.get('fee_base_msat'), 1000),
        'fee_rate_ppm': safe_int(policy.get('fee_rate_milli_msat'), 1),
        'time_lock_delta': safe_int(policy.get('time_lock_delta'), 40),
        'min_htlc_msat': safe_int(policy.get('min_htlc'), None),  # None => don't pass flag
        'max_htlc_msat': safe_int(policy.get('max_htlc_msat'), 0),
        'inbound_base_fee_msat': safe_int(policy.get('inbound_fee_base_msat'), 0),
        'inbound_fee_rate_ppm': safe_int(policy.get('inbound_fee_rate_milli_msat'), 0),
    }


def reset_max_htlc(dry_run=False):
    """Reset all channels to MAX_HTLC_RATIO of capacity while preserving other settings"""
    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")

    # Normalize exclusion list to strings for comparison
    excluded = {str(x) for x in EXCLUDE_CHAN_IDS}
    if excluded:
        print(f"Excluding {len(excluded)} channel(s): {', '.join(sorted(excluded))}\n")

    # Get local node info (fatal if this fails)
    local_info = run_lncli(['getinfo'], exit_on_error=True)
    local_pubkey = local_info.get('identity_pubkey')
    if not local_pubkey:
        print("Error: Could not get local pubkey")
        sys.exit(1)

    # Get all channels (fatal if this fails)
    channels = run_lncli(['listchannels'], exit_on_error=True).get('channels', [])

    success_count = 0
    error_count = 0
    skip_count = 0
    excluded_count = 0

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

        # Skip excluded channels
        if str(short_chan_id) in excluded:
            excluded_count += 1
            print(f"⊘ Channel {short_chan_id}: Excluded")
            continue

        # Skip if no capacity
        if capacity <= 0:
            skip_count += 1
            print(f"⚠ Skipping channel {short_chan_id}: Zero or invalid capacity")
            continue

        # Get current policy (logs its own errors)
        current_policy = get_channel_policy(short_chan_id, local_pubkey)
        if not current_policy:
            error_count += 1
            continue

        # Calculate new max_htlc in millisats
        new_max_htlc_msat = int(capacity * MAX_HTLC_RATIO * 1000)

        old_max_sat = current_policy['max_htlc_msat'] // 1000
        new_max_sat = new_max_htlc_msat // 1000
        inbound_base = current_policy['inbound_base_fee_msat']
        inbound_rate = current_policy['inbound_fee_rate_ppm']
        min_htlc = current_policy['min_htlc_msat']
        min_htlc_display = f"{min_htlc}msat" if min_htlc is not None else "unchanged"

        if dry_run:
            print(f"[DRY RUN] Channel {short_chan_id}: Would reset max_htlc from {old_max_sat:,} to {new_max_sat:,} sats")
            print(f"          Preserving: base_fee={current_policy['base_fee_msat']}msat, "
                  f"fee_rate={current_policy['fee_rate_ppm']}ppm, "
                  f"time_lock_delta={current_policy['time_lock_delta']}, "
                  f"min_htlc={min_htlc_display}, "
                  f"inbound_base={inbound_base}msat, "
                  f"inbound_rate={inbound_rate}ppm")
            success_count += 1
            continue

        try:
            # Build updatechanpolicy command.
            # CRITICAL: inbound fees must be explicitly passed or LND wipes them to 0
            # (lnd issue #8614). base_fee, fee_rate, and time_lock_delta must be
            # specified (no "leave unchanged" option). min_htlc_msat IS omittable
            # (unset = unchanged), which is safer when we couldn't read it.
            cmd = [
                'updatechanpolicy',
                '--chan_point', chan_point,
                '--base_fee_msat', str(current_policy['base_fee_msat']),
                '--fee_rate_ppm', str(current_policy['fee_rate_ppm']),
                '--time_lock_delta', str(current_policy['time_lock_delta']),
                '--max_htlc_msat', str(new_max_htlc_msat),
                '--inbound_base_fee_msat', str(inbound_base),
                '--inbound_fee_rate_ppm', str(inbound_rate),
            ]

            # Only pass min_htlc_msat if we successfully read a value
            if min_htlc is not None:
                cmd.extend(['--min_htlc_msat', str(min_htlc)])

            run_lncli(cmd)

            success_count += 1
            inbound_note = ""
            if inbound_base < 0 or inbound_rate < 0:
                inbound_note = f" [preserved inbound: base={inbound_base}msat, rate={inbound_rate}ppm]"
            print(f"✓ Channel {short_chan_id}: Reset max_htlc from {old_max_sat:,} to {new_max_sat:,} sats "
                  f"({capacity:,} sat capacity){inbound_note}")

        except Exception as e:
            error_count += 1
            print(f"✗ Channel {short_chan_id}: Failed to update - {str(e)}")

    # Summary
    action = "would be" if dry_run else ""
    print(f"\n{'DRY RUN ' if dry_run else ''}Complete: {success_count} channels {action} reset, "
          f"{error_count} errors, {skip_count} skipped, {excluded_count} excluded")
    print(f"Total channels processed: {len(channels)}")


if __name__ == "__main__":
    # Check for dry-run flag
    dry_run = '--dry-run' in sys.argv or '-n' in sys.argv
    reset_max_htlc(dry_run)
