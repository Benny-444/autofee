#!/usr/bin/env python3
"""
Unified Rebalance History Viewer
Shows successful circular rebalances from all sources (LNDg, Regolancer, BOS, etc.)
by querying LND's payment history directly.
"""

import subprocess
import json
import sys
from datetime import datetime
import unicodedata

# Configuration
LNCLI_PATH = "/usr/local/bin/lncli"
DEFAULT_COUNT = 20
BATCH_SIZE = 500  # Payments to fetch per batch
MAX_SEARCH = 100000  # Safety limit - max payments to search


def run_lncli(cmd, exit_on_error=True):
    """Execute lncli command and return parsed JSON."""
    try:
        result = subprocess.run(
            f"{LNCLI_PATH} {cmd}",
            shell=True, capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        if exit_on_error:
            print(f"Error running lncli: {e.stderr}", file=sys.stderr)
            sys.exit(1)
        return None
    except json.JSONDecodeError as e:
        if exit_on_error:
            print(f"Error parsing JSON: {e}", file=sys.stderr)
            sys.exit(1)
        return None


def get_node_alias(pubkey, alias_cache):
    """Get alias for a pubkey, using cache to avoid repeated lookups."""
    if pubkey in alias_cache:
        return alias_cache[pubkey]

    info = run_lncli(f"getnodeinfo {pubkey}", exit_on_error=False)
    if info and "node" in info:
        alias = info["node"].get("alias", pubkey[:16])
    else:
        alias = pubkey[:16]

    alias_cache[pubkey] = alias
    return alias


def get_channel_map():
    """Build mapping of channel IDs to pubkeys."""
    channels = {}

    # Get open channels
    open_chans = run_lncli("listchannels")
    for ch in open_chans.get("channels", []):
        chan_id = ch.get("chan_id")
        pubkey = ch.get("remote_pubkey", "")
        if chan_id:
            channels[chan_id] = pubkey

    # Get closed channels too
    closed_chans = run_lncli("closedchannels")
    for ch in closed_chans.get("channels", []):
        chan_id = ch.get("chan_id")
        pubkey = ch.get("remote_pubkey", "")
        if chan_id and chan_id not in channels:
            channels[chan_id] = pubkey

    return channels


def get_own_pubkey():
    """Get our own node's pubkey."""
    info = run_lncli("getinfo")
    return info.get("identity_pubkey", "")


def is_circular_rebalance(payment, own_pubkey):
    """
    Detect if a payment is a circular rebalance.
    Circular rebalances start and end at our own node.
    """
    htlcs = payment.get("htlcs", [])
    if not htlcs:
        return False, None, None

    for htlc in htlcs:
        if htlc.get("status") != "SUCCEEDED":
            continue

        route = htlc.get("route", {})
        hops = route.get("hops", [])

        if len(hops) < 2:
            continue

        # Check if last hop comes back to us (circular)
        last_hop = hops[-1]
        if last_hop.get("pub_key") == own_pubkey:
            first_hop = hops[0]
            inbound_hop = hops[-2]
            return True, first_hop, inbound_hop

    return False, None, None


def extract_rebalance_info(payment, first_hop, inbound_hop, channel_map, alias_cache):
    """Extract rebalance details from payment and hops."""
    amount = int(payment.get("value_sat", 0))
    fee_sat = int(payment.get("fee_sat", 0))
    ppm = int((fee_sat / amount) * 1_000_000) if amount > 0 else 0

    # Outbound channel - first hop pubkey
    out_pubkey = first_hop.get("pub_key", "")
    out_alias = get_node_alias(out_pubkey, alias_cache)
    out_chan_id = first_hop.get("chan_id", "")

    # Inbound channel - second to last hop pubkey
    in_pubkey = inbound_hop.get("pub_key", "")
    in_alias = get_node_alias(in_pubkey, alias_cache)

    # Timestamp
    ts = int(payment.get("creation_time_ns", 0)) // 1_000_000_000
    if ts == 0:
        ts = int(payment.get("creation_date", 0))

    return {
        "timestamp": ts,
        "datetime": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "Unknown",
        "amount": amount,
        "fee_sat": fee_sat,
        "ppm": ppm,
        "out_alias": out_alias,
        "out_chan_id": out_chan_id,
        "in_alias": in_alias,
        "payment_hash": payment.get("payment_hash", "")[:16]
    }


def get_rebalances(count=DEFAULT_COUNT, in_filter=None, out_filter=None):
    """Fetch and filter circular rebalances from payment history with pagination."""
    print("Fetching node info...", file=sys.stderr)
    own_pubkey = get_own_pubkey()

    print("Building channel map...", file=sys.stderr)
    channel_map = get_channel_map()
    alias_cache = {}

    print("Analyzing payments for circular rebalances...", file=sys.stderr)

    rebalances = []
    total_searched = 0
    
    # Get the latest payment index first
    initial = run_lncli("listpayments --max_payments 1")
    last_idx = int(initial.get("last_index_offset", 0))
    
    if last_idx == 0:
        print("  No payments found.", file=sys.stderr)
        return []
    
    # Start from the end and work backwards
    # index_offset with no paginate_forwards returns payments BEFORE the offset
    current_offset = last_idx + 1  # Start just past the last payment
    
    while len(rebalances) < count and total_searched < MAX_SEARCH and current_offset > 0:
        # Fetch batch - default order is newest first when using index_offset
        cmd = f"listpayments --max_payments {BATCH_SIZE} --index_offset {current_offset}"
        
        payments_data = run_lncli(cmd)
        payments = payments_data.get("payments", [])
        
        if not payments:
            break  # No more payments
        
        batch_count = len(payments)
        total_searched += batch_count
        
        # Get offset for next older batch
        first_idx = int(payments_data.get("first_index_offset", 0))
        
        # Payments are returned oldest-first in this mode, so reverse for newest-first
        payments.reverse()
        
        for payment in payments:
            if payment.get("status") != "SUCCEEDED":
                continue

            is_circular, first_hop, inbound_hop = is_circular_rebalance(payment, own_pubkey)

            if is_circular and first_hop and inbound_hop:
                info = extract_rebalance_info(payment, first_hop, inbound_hop, channel_map, alias_cache)

                # Apply filters
                if in_filter and in_filter.lower() not in info['in_alias'].lower():
                    continue
                if out_filter and out_filter.lower() not in info['out_alias'].lower():
                    continue

                rebalances.append(info)

                if len(rebalances) >= count:
                    break
        
        # Progress feedback
        if len(rebalances) < count:
            print(f"  Searched {total_searched:,} payments, found {len(rebalances)} rebalances...", file=sys.stderr)
        
        # Move to older payments
        current_offset = first_idx
        
        # If we've reached the beginning, stop
        if first_idx == 0:
            break

    # Final status
    if len(rebalances) >= count:
        print(f"  Found {count} rebalances after searching {total_searched:,} payments.", file=sys.stderr)
    elif total_searched >= MAX_SEARCH:
        print(f"  Reached search limit ({MAX_SEARCH:,} payments). Found {len(rebalances)} rebalances.", file=sys.stderr)
    else:
        print(f"  Exhausted payment history ({total_searched:,} payments). Found {len(rebalances)} rebalances.", file=sys.stderr)
    
    return rebalances


def format_sats(sats):
    """Format satoshis with thousands separator."""
    return f"{sats:,}"


def display_width(s):
    """Calculate actual display width accounting for wide chars (emojis, CJK)."""
    width = 0
    for char in s:
        if unicodedata.east_asian_width(char) in ('W', 'F'):
            width += 2
        elif unicodedata.category(char) in ('So', 'Sk', 'Sm'):
            # Symbols including emojis
            width += 2
        else:
            width += 1
    return width


def truncate(s, length=20):
    """Truncate string to specified display width."""
    width = 0
    result = []
    for char in s:
        char_width = 2 if (unicodedata.east_asian_width(char) in ('W', 'F') or
                          unicodedata.category(char) in ('So', 'Sk', 'Sm')) else 1
        if width + char_width > length - 2:
            return ''.join(result) + ".."
        result.append(char)
        width += char_width
    return s


def pad_to_width(s, width):
    """Pad string to specified display width."""
    current = display_width(s)
    if current >= width:
        return s
    return s + ' ' * (width - current)


def print_rebalances(rebalances, in_filter=None, out_filter=None):
    """Print rebalances in a formatted table."""
    if not rebalances:
        msg = "No circular rebalances found"
        filters = []
        if in_filter:
            filters.append(f"in='{in_filter}'")
        if out_filter:
            filters.append(f"out='{out_filter}'")
        if filters:
            msg += f" matching {', '.join(filters)}"
        print(f"\n{msg} in payment history.")
        return

    print(f"\n{'=' * 105}")
    print(f"{'Timestamp':<20} {'Out Channel':<20} {'In Channel':<20} {'Amount':>12} {'Fee':>8} {'PPM':>6}")
    print("=" * 105)

    total_amount = 0
    total_fees = 0

    for rb in rebalances:
        total_amount += rb["amount"]
        total_fees += rb["fee_sat"]

        out_col = pad_to_width(truncate(rb['out_alias'], 20), 20)
        in_col = pad_to_width(truncate(rb['in_alias'], 20), 20)

        print(f"{rb['datetime']:<20} "
              f"{out_col} "
              f"{in_col} "
              f"{format_sats(rb['amount']):>12} "
              f"{format_sats(rb['fee_sat']):>8} "
              f"{rb['ppm']:>6}")

    print("-" * 105)
    avg_ppm = int((total_fees / total_amount) * 1_000_000) if total_amount > 0 else 0
    print(f"{'TOTALS':<20} {'':<20} {'':<20} "
          f"{format_sats(total_amount):>12} "
          f"{format_sats(total_fees):>8} "
          f"{avg_ppm:>6}")
    filters = []
    if out_filter:
        filters.append(f"out='{out_filter}'")
    if in_filter:
        filters.append(f"in='{in_filter}'")
    filter_note = f" ({', '.join(filters)})" if filters else ""
    print(f"\n{len(rebalances)} rebalances{filter_note} | Avg fee: {avg_ppm} ppm")


def main():
    count = DEFAULT_COUNT
    in_filter = None
    out_filter = None

    # Parse arguments
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--in' and i + 1 < len(args):
            in_filter = args[i + 1]
            i += 2
        elif args[i] == '--out' and i + 1 < len(args):
            out_filter = args[i + 1]
            i += 2
        elif args[i] in ('-h', '--help'):
            print(f"Usage: {sys.argv[0]} [count] [--in <channel>] [--out <channel>]")
            print(f"  count           Number of rebalances to show (default: {DEFAULT_COUNT})")
            print(f"  --in <channel>  Filter by inbound channel (partial match, case-insensitive)")
            print(f"  --out <channel> Filter by outbound channel (partial match, case-insensitive)")
            print(f"\nExamples:")
            print(f"  {sys.argv[0]} 50")
            print(f"  {sys.argv[0]} --in kraken")
            print(f"  {sys.argv[0]} --out boltz")
            print(f"  {sys.argv[0]} 30 --out boltz --in acinq")
            sys.exit(0)
        else:
            try:
                count = int(args[i])
            except ValueError:
                print(f"Unknown argument: {args[i]}", file=sys.stderr)
                sys.exit(1)
            i += 1

    rebalances = get_rebalances(count, in_filter, out_filter)
    print_rebalances(rebalances, in_filter, out_filter)


if __name__ == "__main__":
    main()