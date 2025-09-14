#!/usr/bin/env python3
"""
Autofee Dashboard Report Generator
Generates a focused dashboard showing channel status and fee change history
"""
import json
import subprocess
import os
import sys
import argparse
import re
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import html
import textwrap

# Configuration
BASE_DIR = os.path.expanduser('~/autofee')
AVG_FEE_FILE = os.path.join(BASE_DIR, 'avg_fees.json')
NEGINB_STATE_FILE = os.path.join(BASE_DIR, 'neginb_state.json')
STAGNANT_STATE_FILE = os.path.join(BASE_DIR, 'stagnant_state.json')
CHARGE_INI_FILE = os.path.join(BASE_DIR, 'dynamic_charge.ini')
LOG_FILES = {
    'wrapper': os.path.join(BASE_DIR, 'autofee_wrapper.log'),
    'neginb': os.path.join(BASE_DIR, 'autofee_neginb_wrapper.log'),
    'stagnant': os.path.join(BASE_DIR, 'autofee_stagnant_wrapper.log'),
    'maxhtlc': os.path.join(BASE_DIR, 'autofee_maxhtlc_wrapper.log')
}

# Configuration constants from wrapper scripts
CONFIG = {
    'ema_alpha': 0.15,  # From autofee_wrapper.py: ALPHA
    'min_avg_fee': 10,  # From autofee_wrapper.py: MIN_AVG_FEE
    'days_back': 14,    # From autofee_wrapper.py: DAYS_BACK
    'adjustment_factor': 0.05,  # From autofee_wrapper.py: ADJUSTMENT_FACTOR
    'neg_inb_trigger': 30,      # From autofee_neginb_wrapper.py: NEGATIVE_INBOUND_TRIGGER
    'neg_inb_remove': 60,       # From autofee_neginb_wrapper.py: NEGATIVE_INBOUND_REMOVE
    'initial_inbound_pct': 50,  # From autofee_neginb_wrapper.py: INITIAL_INBOUND_PCT
    'increment_pct': 2,         # From autofee_neginb_wrapper.py: INCREMENT_PCT
    'max_inbound_pct': 80,      # From autofee_neginb_wrapper.py: MAX_INBOUND_PCT
    'stagnant_ratio_threshold': 0.30,  # From autofee_stagnant_wrapper.py: STAGNANT_RATIO_THRESHOLD
    'stagnant_hours': 72,       # From autofee_stagnant_wrapper.py: STAGNANT_HOURS
    'stagnant_reduction_pct': 5,  # From autofee_stagnant_wrapper.py: STAGNANT_REDUCTION_PCT
    'max_htlc_ratio': 0.98,     # From autofee_maxhtlc_wrapper.py: MAX_HTLC_RATIO
    'exclude_chan_ids': []      # From autofee_wrapper.py: EXCLUDE_CHAN_IDS
}

# Terminal colors
COLORS = {
    'GREEN': '\033[92m',
    'RED': '\033[91m',
    'BLUE': '\033[94m',
    'YELLOW': '\033[93m',
    'BOLD': '\033[1m',
    'END': '\033[0m'
}

def colorize(text, color):
    """Apply terminal color to text"""
    if sys.stdout.isatty():
        return f"{COLORS.get(color, '')}{text}{COLORS['END']}"
    return text

def run_lncli(args):
    """Execute lncli command and parse JSON output"""
    try:
        output = subprocess.check_output(['lncli'] + args, stderr=subprocess.STDOUT)
        return json.loads(output.decode())
    except Exception as e:
        logging.error(f"Error running lncli {args}: {str(e)}")
        return None

def load_json_file(filepath):
    """Safely load JSON file"""
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error loading {filepath}: {str(e)}")
    return {}

def parse_ini_file(filepath):
    """Parse charge-lnd INI file"""
    sections = {}
    try:
        if not os.path.exists(filepath):
            return sections
        current_section = None
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    sections[current_section] = {}
                elif '=' in line and current_section:
                    key, value = line.split('=', 1)
                    sections[current_section][key.strip()] = value.strip()
    except Exception as e:
        logging.error(f"Error parsing INI file: {str(e)}")
    return sections

def scid_to_x_format(scid):
    """Convert decimal SCID to x format"""
    try:
        scid_int = int(scid)
        block_height = scid_int >> 40
        tx_index = (scid_int >> 16) & 0xFFFFFF
        output_index = scid_int & 0xFFFF
        return f"{block_height}x{tx_index}x{output_index}"
    except:
        return None

def get_channel_alias(peer_pubkey):
    """Get channel alias from node info"""
    try:
        node_info = run_lncli(['getnodeinfo', peer_pubkey])
        if node_info and 'node' in node_info:
            return node_info['node'].get('alias', peer_pubkey[:16])
    except:
        pass
    return peer_pubkey[:16]

def parse_log_entries(filepath, channel_id, scid, days_back=3):
    """Parse log entries for a specific channel using chan_id or scid"""
    entries = []
    if not os.path.exists(filepath):
        return entries

    cutoff_time = datetime.now() - timedelta(days=days_back)
    log_type = os.path.basename(filepath).replace('.log', '')

    try:
        with open(filepath, 'r') as f:
            for line in f:
                # Skip lines before cutoff to optimize
                timestamp_str = ' '.join(line.split(' ')[:2])
                try:
                    timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S,%f')
                    if timestamp < cutoff_time:
                        continue
                except:
                    try:
                        # Fallback for missing microseconds
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                        if timestamp < cutoff_time:
                            continue
                    except:
                        logging.error(f"Invalid timestamp format in {filepath}: {timestamp_str}")
                        continue

                # Match based on log type
                if log_type == 'autofee_neginb_wrapper':
                    if str(scid) in line:
                        entries.append({
                            'timestamp': timestamp,
                            'message': line.strip()
                        })
                elif str(channel_id) in line or str(scid) in line:
                    entries.append({
                        'timestamp': timestamp,
                        'message': line.strip()
                    })
    except Exception as e:
        logging.error(f"Error parsing log {filepath}: {str(e)}")

    return sorted(entries, key=lambda x: x['timestamp'], reverse=True)

def simplify_log_message(message, log_type):
    """Simplify log messages for UI display"""
    try:
        msg_part = message.split(': ', 2)[-1] if ': ' in message else message

        if log_type == 'neginb':
            if 'Removing negative inbound fee' in msg_part:
                return "Removing inbound fee"
            elif 'Initializing negative inbound fee' in msg_part:
                match = re.search(r'to (-?\d+) ppm \((\d+)%', msg_part)
                if match:
                    return f"Initializing inbound {match.group(1)} ({match.group(2)}%)"
            elif 'Incrementing negative inbound' in msg_part:
                match = re.search(r'from (-?\d+) to (-?\d+) ppm', msg_part)
                if match:
                    return f"Incrementing inbound {match.group(1)} → {match.group(2)}"
            elif 'Keeping max negative inbound' in msg_part:
                match = re.search(r'at (-?\d+) ppm', msg_part)
                if match:
                    return f"Keeping max inbound {match.group(1)}"
            elif 'Below threshold' in msg_part and 'never been above' in msg_part:
                return "Below threshold, never above"
            elif 'Adjusting negative inbound' in msg_part:
                match = re.search(r'from (-?\d+) to (-?\d+) ppm', msg_part)
                if match:
                    return f"Adjusting inbound {match.group(1)} → {match.group(2)}"
            elif 'Maintaining negative inbound' in msg_part:
                match = re.search(r'at (-?\d+) ppm', msg_part)
                if match:
                    return f"Maintaining inbound {match.group(1)}"

        elif log_type == 'stagnant':
            if 'Recovered from stagnant state' in msg_part:
                return "Recovered from stagnant"
            elif 'Became stagnant' in msg_part:
                match = re.search(r'for (\d+d \d+h)', msg_part)
                if match:
                    return f"Became stagnant ({match.group(1)})"
            elif 'Reduced outbound fee' in msg_part:
                match = re.search(r'from (\d+) to (\d+) ppm', msg_part)
                if match:
                    return f"Reduced outbound {match.group(1)} → {match.group(2)}"
            elif 'Reduced inbound fee' in msg_part:
                match = re.search(r'from (-?\d+) to (-?\d+) ppm', msg_part)
                if match:
                    return f"Reduced inbound {match.group(1)} → {match.group(2)}"
            elif 'Removed inbound fee' in msg_part:
                return "Removed inbound fee"

        elif log_type == 'maxhtlc':
            if 'max_htlc:' in msg_part:
                match = re.search(r'max_htlc: ([\d,]+) -> ([\d,]+) sats \(([+-]?\d+\.\d+)%\)', msg_part)
                if match:
                    return f"Updated {match.group(1)} → {match.group(2)} sats ({match.group(3)}%)"
            elif 'max_htlc set to' in msg_part:
                match = re.search(r'set to ([\d,]+) sats', msg_part)
                if match:
                    return f"Set to {match.group(1)} sats (new)"

        elif log_type == 'wrapper':
            if 'avg_fee=' in msg_part:
                match = re.search(r'avg_fee=(\d+), ratio=([\d.]+), current=(\d+), target=(\d+), new=(\d+)', msg_part)
                if match:
                    current = int(match.group(3))
                    new = int(match.group(5))
                    change = new - current
                    return f"Fee change: {current} → {new} ({change:+d})"

        return msg_part[:50] + "..." if len(msg_part) > 50 else msg_part

    except:
        return msg_part[:50] + "..." if len(msg_part) > 50 else msg_part

def extract_fee_values_from_logs(channel_id, scid, days_back=3):
    """Extract current fee values from wrapper logs"""
    wrapper_entries = parse_log_entries(LOG_FILES['wrapper'], channel_id, scid, days_back)

    values = {
        'avg_fee': '--',
        'current_fee': '--',
        'target_fee': '--',
        'new_fee': '--'
    }

    for entry in wrapper_entries:
        msg = entry['message']
        if 'avg_fee=' in msg:
            try:
                match = re.search(r'avg_fee=(\d+), ratio=([\d.]+), current=(\d+), target=(\d+), new=(\d+)', msg)
                if match:
                    values['avg_fee'] = int(match.group(1))
                    values['current_fee'] = int(match.group(3))
                    values['target_fee'] = int(match.group(4))
                    values['new_fee'] = int(match.group(5))
                    break
            except:
                continue

    return values

def generate_capacity_bar(local_balance, capacity, width=15):
    """Generate ASCII capacity bar"""
    if capacity == 0:
        return "—" * width

    ratio = local_balance / capacity
    filled = int(ratio * width)

    bar = "█" * filled + "░" * (width - filled)
    return f"{ratio*100:.0f}% {bar} {capacity//1000}k"

def get_channel_data():
    """Collect all channel data for the dashboard"""
    avg_fees = load_json_file(AVG_FEE_FILE)
    neginb_state = load_json_file(NEGINB_STATE_FILE)
    stagnant_state = load_json_file(STAGNANT_STATE_FILE)
    ini_sections = parse_ini_file(CHARGE_INI_FILE)

    channels_data = run_lncli(['listchannels'])
    if not channels_data or 'channels' not in channels_data:
        return []

    channels = []

    for chan in channels_data['channels']:
        chan_id = chan.get('chan_id')
        scid = chan.get('scid')

        if not chan_id or not scid:
            continue

        peer_pubkey = chan.get('remote_pubkey', '')
        alias = get_channel_alias(peer_pubkey)

        capacity = int(chan.get('capacity', 0))
        local_balance = int(chan.get('local_balance', 0))

        is_active = chan.get('active', False)
        is_stagnant = stagnant_state.get(str(scid), {}).get('is_stagnant', False)
        status = 'Stagnant' if is_stagnant else ('Active' if is_active else 'Inactive')

        fee_values = extract_fee_values_from_logs(chan_id, scid)

        neginb_info = neginb_state.get(str(scid), {})
        inbound_disc = neginb_info.get('inbound_fee', 0)

        scid_x = scid_to_x_format(scid)
        ini_section = f"autofee-{scid_x}" if scid_x else None
        max_htlc = '--'
        if ini_section and ini_section in ini_sections:
            max_htlc_val = ini_sections[ini_section].get('max_htlc_msat', '')
            if max_htlc_val:
                try:
                    max_htlc = int(max_htlc_val) // 1000  # Convert to sats
                except (ValueError, TypeError):
                    max_htlc = '--'

        log_entries = {
            'wrapper': parse_log_entries(LOG_FILES['wrapper'], chan_id, scid),
            'neginb': parse_log_entries(LOG_FILES['neginb'], chan_id, scid),
            'stagnant': parse_log_entries(LOG_FILES['stagnant'], chan_id, scid),
            'maxhtlc': parse_log_entries(LOG_FILES['maxhtlc'], chan_id, scid)
        }

        channel_data = {
            'alias': alias,
            'chan_id': chan_id,
            'scid': scid,
            'capacity': capacity,
            'local_balance': local_balance,
            'status': status,
            'is_active': is_active,
            'is_stagnant': is_stagnant,
            'avg_fee': fee_values['avg_fee'],
            'current_fee': fee_values['current_fee'],
            'target_fee': fee_values['target_fee'],
            'new_fee': fee_values['new_fee'],
            'inbound_disc': inbound_disc,
            'max_htlc': max_htlc,
            'log_entries': log_entries
        }

        channels.append(channel_data)

    return sorted(channels, key=lambda x: x['alias'].lower())

def generate_html_report(channels):
    """Generate HTML dashboard"""
    config_info = CONFIG
    exclude_channels = config_info['exclude_chan_ids'] or ["None"]

    html = textwrap.dedent(f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Lightning Node Autofee Dashboard</title>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f5f5f5;
                color: #333;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                display: grid;
                grid-template-columns: 280px 1fr;
                gap: 20px;
            }}
            .config-panel {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                height: fit-content;
                position: sticky;
                top: 20px;
            }}
            .config-section {{
                margin-bottom: 20px;
            }}
            .config-section h3 {{
                margin: 0 0 10px 0;
                font-size: 14px;
                font-weight: 600;
                color: #2c3e50;
                border-bottom: 2px solid #3498db;
                padding-bottom: 5px;
            }}
            .config-item {{
                display: flex;
                justify-content: space-between;
                margin: 5px 0;
                font-size: 12px;
            }}
            .config-label {{
                color: #666;
            }}
            .config-value {{
                font-weight: 500;
                color: #2c3e50;
            }}
            .main-panel {{
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .dashboard-header {{
                background: #2c3e50;
                color: white;
                padding: 20px;
                text-align: center;
            }}
            .dashboard-header h1 {{
                margin: 0;
                font-size: 24px;
            }}
            .dashboard-header p {{
                margin: 5px 0 0 0;
                opacity: 0.8;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 12px;
            }}
            th {{
                background: #34495e;
                color: white;
                padding: 10px 6px;
                text-align: left;
                font-weight: 500;
                position: sticky;
                top: 0;
                z-index: 10;
                font-size: 11px;
            }}
            td {{
                padding: 6px;
                border-bottom: 1px solid #ecf0f1;
                vertical-align: top;
                font-size: 11px;
            }}
            tr:hover {{
                background: #f8f9fa;
            }}
            .channel-name {{
                font-weight: 500;
                color: #2c3e50;
            }}
            .capacity-bar {{
                font-family: monospace;
                font-size: 9px;
                white-space: nowrap;
            }}
            .status-active {{ color: #27ae60; font-weight: 500; }}
            .status-stagnant {{ color: #f39c12; font-weight: 500; }}
            .status-inactive {{ color: #e74c3c; font-weight: 500; }}
            .fee-increase {{ color: #27ae60; font-weight: 500; }}
            .fee-decrease {{ color: #e74c3c; font-weight: 500; }}
            .fee-neutral {{ color: #7f8c8d; }}
            .dropdown-arrow {{
                cursor: pointer;
                user-select: none;
                font-family: monospace;
                font-weight: bold;
                color: #3498db;
                margin-left: 5px;
            }}
            .dropdown-arrow:hover {{
                color: #2980b9;
            }}
            .dropdown-content {{
                display: none;
                background: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                margin: 3px 0;
                max-height: 150px;
                overflow-y: auto;
                position: relative;
                z-index: 100;
            }}
            .dropdown-content.show {{
                display: block;
            }}
            .log-entry {{
                padding: 4px 6px;
                border-bottom: 1px solid #e9ecef;
                font-size: 10px;
            }}
            .log-entry:last-child {{
                border-bottom: none;
            }}
            .log-timestamp {{
                color: #6c757d;
                font-family: monospace;
                font-size: 9px;
            }}
            .log-message {{
                margin-left: 8px;
                color: #495057;
            }}
            .no-data {{
                color: #6c757d;
                font-style: italic;
                padding: 8px;
                text-align: center;
            }}
            .detail-text {{
                font-size: 9px;
                color: #6c757d;
                margin-top: 2px;
            }}
        </style>
        <script>
            function toggleDropdown(id) {{
                const dropdown = document.getElementById(id);
                document.querySelectorAll('.dropdown-content.show').forEach(d => {{
                    if (d.id !== id) {{
                        d.classList.remove('show');
                        const arrow = d.previousElementSibling;
                        arrow.textContent = '▶';
                    }}
                }});
                dropdown.classList.toggle('show');
                const arrow = dropdown.previousElementSibling;
                arrow.textContent = dropdown.classList.contains('show') ? '▼' : '▶';
            }}
        </script>
    </head>
    <body>
        <div class="container">
            <div class="config-panel">
                <div class="config-section">
                    <h3>Outbound Settings</h3>
                    <div class="config-item">
                        <span class="config-label">EMA Alpha</span>
                        <span class="config-value">{config_info['ema_alpha']}</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Min Avg Fee</span>
                        <span class="config-value">{config_info['min_avg_fee']} ppm</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Days Back</span>
                        <span class="config-value">{config_info['days_back']}</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Adj Increment</span>
                        <span class="config-value">{config_info['adjustment_factor']*100:.1f}%</span>
                    </div>
                </div>
                <div class="config-section">
                    <h3>Negative Inbound Settings</h3>
                    <div class="config-item">
                        <span class="config-label">Trigger</span>
                        <span class="config-value">{config_info['neg_inb_trigger']}%</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Remove</span>
                        <span class="config-value">{config_info['neg_inb_remove']}%</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Initial %</span>
                        <span class="config-value">{config_info['initial_inbound_pct']}%</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Increment %</span>
                        <span class="config-value">{config_info['increment_pct']}%</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Max Inbound %</span>
                        <span class="config-value">{config_info['max_inbound_pct']}%</span>
                    </div>
                </div>
                <div class="config-section">
                    <h3>Stagnant Settings</h3>
                    <div class="config-item">
                        <span class="config-label">Trigger</span>
                        <span class="config-value">{config_info['stagnant_ratio_threshold']*100:.0f}%</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Hours</span>
                        <span class="config-value">{config_info['stagnant_hours']}</span>
                    </div>
                    <div class="config-item">
                        <span class="config-label">Reduction %</span>
                        <span class="config-value">{config_info['stagnant_reduction_pct']}%</span>
                    </div>
                </div>
                <div class="config-section">
                    <h3>Max HTLC Settings</h3>
                    <div class="config-item">
                        <span class="config-label">Max HTLC Ratio</span>
                        <span class="config-value">{config_info['max_htlc_ratio']*100:.0f}%</span>
                    </div>
                </div>
                <div class="config-section">
                    <h3>Included Channels</h3>
                    <div class="config-item">
                        <span class="config-label">All</span>
                    </div>
                </div>
                <div class="config-section">
                    <h3>Excluded Channels</h3>
                    {''.join(f'<div class="config-item"><span class="config-label">{html.escape(str(scid))}</span></div>' for scid in exclude_channels)}
                </div>
            </div>
            <div class="main-panel">
                <div class="dashboard-header">
                    <h1>⚡ Lightning Node Autofee Dashboard</h1>
                    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(channels)} channels</p>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 120px;">Channel</th>
                            <th style="width: 140px;">Capacity</th>
                            <th style="width: 80px;">Status</th>
                            <th style="width: 80px;">Avg Fee</th>
                            <th style="width: 80px;">Current Fee</th>
                            <th style="width: 80px;">Target Fee</th>
                            <th style="width: 80px;">New Fee</th>
                            <th style="width: 80px;">Inbound Disc</th>
                            <th style="width: 80px;">Max HTLC</th>
                        </tr>
                    </thead>
                    <tbody>
    """)

    for i, chan in enumerate(channels):
        capacity_bar = generate_capacity_bar(chan['local_balance'], chan['capacity'])
        status_class = f"status-{chan['status'].lower()}"

        def get_fee_change_info(current, new):
            if current == '--' or new == '--':
                return 'fee-neutral', '--'
            try:
                curr_val = int(current)
                new_val = int(new)
                if new_val == 0 and curr_val != 0:
                    return 'fee-decrease', '-100%'
                change = new_val - curr_val
                if change > 0:
                    pct = (change / curr_val * 100) if curr_val > 0 else 0
                    return 'fee-increase', f"+{change} (+{pct:.1f}%)"
                elif change < 0:
                    pct = (change / curr_val * 100) if curr_val > 0 else 0
                    return 'fee-decrease', f"{change} ({pct:.1f}%)"
                else:
                    return 'fee-neutral', "No change"
            except:
                return 'fee-neutral', '--'

        new_fee_class, new_fee_change = get_fee_change_info(chan['current_fee'], chan['new_fee'])
        inbound_text = f"{chan['inbound_disc']}" if chan['inbound_disc'] != 0 else "--"

        html += textwrap.dedent(f"""
            <tr>
                <td class="channel-name">{html.escape(chan['alias'][:18])}</td>
                <td>
                    <div class="capacity-bar">{capacity_bar}</div>
                </td>
                <td class="{status_class}">{chan['status']}</td>
                <td>
                    {chan['avg_fee']}
                    <span class="dropdown-arrow" onclick="toggleDropdown('avg-{i}')">▶</span>
                    <div id="avg-{i}" class="dropdown-content">
            """)

        wrapper_entries = chan['log_entries']['wrapper'][:10]
        if wrapper_entries:
            for entry in wrapper_entries:
                timestamp = entry['timestamp'].strftime('%m-%d %H:%M')
                simplified = simplify_log_message(entry['message'], 'wrapper')
                html += f"""
                    <div class="log-entry">
                        <span class="log-timestamp">{timestamp}</span>
                        <span class="log-message">{html.escape(simplified)}</span>
                    </div>
                """
        else:
            html += '<div class="no-data">No recent changes</div>'

        html += textwrap.dedent(f"""
                </div>
            </td>
            <td>
                {chan['current_fee']}
                <span class="dropdown-arrow" onclick="toggleDropdown('current-{i}')">▶</span>
                <div id="current-{i}" class="dropdown-content">
            """)

        if wrapper_entries:
            for entry in wrapper_entries:
                timestamp = entry['timestamp'].strftime('%m-%d %H:%M')
                simplified = simplify_log_message(entry['message'], 'wrapper')
                html += f"""
                    <div class="log-entry">
                        <span class="log-timestamp">{timestamp}</span>
                        <span class="log-message">{html.escape(simplified)}</span>
                    </div>
                """
        else:
            html += '<div class="no-data">No recent changes</div>'

        html += textwrap.dedent(f"""
                </div>
            </td>
            <td>
                {chan['target_fee']}
                <div class="detail-text">Details from autofee_{'stagnant' if chan['is_stagnant'] else 'wrapper'}_wrapper.log</div>
                <span class="dropdown-arrow" onclick="toggleDropdown('target-{i}')">▶</span>
                <div id="target-{i}" class="dropdown-content">
            """)

        target_entries = chan['log_entries']['stagnant'][:10] if chan['is_stagnant'] else chan['log_entries']['wrapper'][:10]
        log_type = 'stagnant' if chan['is_stagnant'] else 'wrapper'

        if target_entries:
            for entry in target_entries:
                timestamp = entry['timestamp'].strftime('%m-%d %H:%M')
                simplified = simplify_log_message(entry['message'], log_type)
                html += f"""
                    <div class="log-entry">
                        <span class="log-timestamp">{timestamp}</span>
                        <span class="log-message">{html.escape(simplified)}</span>
                    </div>
                """
        else:
            html += '<div class="no-data">No recent changes</div>'

        html += textwrap.dedent(f"""
                </div>
            </td>
            <td class="{new_fee_class}">
                {chan['new_fee']}
                <div class="detail-text">{new_fee_change}</div>
                <span class="dropdown-arrow" onclick="toggleDropdown('new-{i}')">▶</span>
                <div id="new-{i}" class="dropdown-content">
            """)

        new_fee_entries = chan['log_entries']['stagnant'][:10] if chan['is_stagnant'] else chan['log_entries']['wrapper'][:10]
        new_log_type = 'stagnant' if chan['is_stagnant'] else 'wrapper'

        if new_fee_entries:
            for entry in new_fee_entries:
                timestamp = entry['timestamp'].strftime('%m-%d %H:%M')
                simplified = simplify_log_message(entry['message'], new_log_type)
                html += f"""
                    <div class="log-entry">
                        <span class="log-timestamp">{timestamp}</span>
                        <span class="log-message">{html.escape(simplified)}</span>
                    </div>
                """
        else:
            html += '<div class="no-data">No recent changes</div>'

        html += textwrap.dedent(f"""
                </div>
            </td>
            <td>
                {inbound_text}
                <div class="detail-text">Details from autofee_neginb_wrapper.log</div>
                <span class="dropdown-arrow" onclick="toggleDropdown('inbound-{i}')">▶</span>
                <div id="inbound-{i}" class="dropdown-content">
            """)

        neginb_entries = chan['log_entries']['neginb'][:10]
        if neginb_entries:
            for entry in neginb_entries:
                timestamp = entry['timestamp'].strftime('%m-%d %H:%M')
                simplified = simplify_log_message(entry['message'], 'neginb')
                html += f"""
                    <div class="log-entry">
                        <span class="log-timestamp">{timestamp}</span>
                        <span class="log-message">{html.escape(simplified)}</span>
                    </div>
                """
        else:
            html += '<div class="no-data">No recent changes</div>'

        html += textwrap.dedent(f"""
                </div>
            </td>
            <td>
                {chan['max_htlc']}
                <span class="dropdown-arrow" onclick="toggleDropdown('maxhtlc-{i}')">▶</span>
                <div id="maxhtlc-{i}" class="dropdown-content">
            """)

        maxhtlc_entries = chan['log_entries']['maxhtlc'][:10]
        if maxhtlc_entries:
            for entry in maxhtlc_entries:
                timestamp = entry['timestamp'].strftime('%m-%d %H:%M')
                simplified = simplify_log_message(entry['message'], 'maxhtlc')
                html += f"""
                    <div class="log-entry">
                        <span class="log-timestamp">{timestamp}</span>
                        <span class="log-message">{html.escape(simplified)}</span>
                    </div>
                """
        else:
            html += '<div class="no-data">No recent changes</div>'

        html += textwrap.dedent(f"""
                </div>
            </td>
        </tr>
        """)

    html += textwrap.dedent("""
            </tbody>
        </table>
    </div>
</div>
</body>
</html>
    """)

    return html

def generate_terminal_report(channels):
    """Generate simplified terminal report"""
    print(f"\n{colorize('⚡ LIGHTNING NODE AUTOFEE DASHBOARD', 'BOLD')}")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {len(channels)} channels")
    print("=" * 120)

    print(f"{'Channel':<20} {'Capacity':<15} {'Status':<10} {'Avg':<6} {'Curr':<6} {'Targ':<6} {'New':<6} {'InDisc':<8} {'MaxHTLC':<8}")
    print("-" * 120)

    for chan in channels:
        capacity_pct = (chan['local_balance'] / chan['capacity'] * 100) if chan['capacity'] > 0 else 0
        capacity_str = f"{capacity_pct:.0f}% {chan['capacity']//1000}k"

        status_color = 'GREEN' if chan['is_active'] else ('YELLOW' if chan['is_stagnant'] else 'RED')
        status = colorize(chan['status'][:8], status_color)

        try:
            if chan['current_fee'] != '--' and chan['new_fee'] != '--':
                change = int(chan['new_fee']) - int(chan['current_fee'])
                new_fee_color = 'GREEN' if change > 0 else ('RED' if change < 0 else None)
                new_fee_str = colorize(str(chan['new_fee']), new_fee_color) if new_fee_color else str(chan['new_fee'])
            else:
                new_fee_str = str(chan['new_fee'])
        except:
            new_fee_str = str(chan['new_fee'])

        print(f"{chan['alias'][:19]:<20} {capacity_str:<15} {status:<10} "
              f"{str(chan['avg_fee']):<6} {str(chan['current_fee']):<6} {str(chan['target_fee']):<6} "
              f"{new_fee_str:<6} {str(chan['inbound_disc']):<8} {str(chan['max_htlc']):<8}")

    print("=" * 120)

    active_count = sum(1 for c in channels if c['is_active'])
    stagnant_count = sum(1 for c in channels if c['is_stagnant'])
    inbound_count = sum(1 for c in channels if c['inbound_disc'] != 0)

    print(f"\nSummary: {active_count} active, {stagnant_count} stagnant, {inbound_count} with inbound discounts")
    print(f"Use --format html for detailed dropdown history")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Generate autofee dashboard report')
    parser.add_argument('--format', type=str, default='html',
                        choices=['html', 'terminal', 'both'],
                        help='Output format')
    parser.add_argument('--output', type=str, default=None,
                        help='Output file for HTML report')
    parser.add_argument('--chan-ids', type=str, nargs='+', default=[],
                        help='Specific channel IDs to report on')

    args = parser.parse_args()

    logging.basicConfig(
        filename=os.path.join(BASE_DIR, 'autofee_report.log'),
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s'
    )

    try:
        print("Gathering channel data...")
        channels = get_channel_data()

        if not channels:
            print("Error: No channel data found")
            return 1

        if args.chan_ids:
            channels = [c for c in channels if c['chan_id'] in args.chan_ids or str(c['scid']) in args.chan_ids]
            if not channels:
                print(f"Error: No channels found matching IDs: {args.chan_ids}")
                return 1

        if args.format in ['terminal', 'both']:
            generate_terminal_report(channels)

        if args.format in ['html', 'both']:
            html_content = generate_html_report(channels)
            if args.output:
                output_file = args.output
            else:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_file = os.path.join(BASE_DIR, f'autofee_dashboard_{timestamp}.html')

            with open(output_file, 'w') as f:
                f.write(html_content)
            print(f"\nHTML dashboard saved to: {output_file}")

        logging.info(f"Dashboard generated successfully for {len(channels)} channels")
        return 0

    except Exception as e:
        logging.error(f"Error generating dashboard: {str(e)}")
        print(f"Error generating dashboard: {str(e)}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
