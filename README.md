# LND Autofee

LND Autofee is an automated Lightning Network channel fee management system that implements **inverse liquidity-based pricing** using Exponential Moving Average (EMA) calculations of true fees. It integrates with the charge-lnd tool to dynamically adjust channel routing fees based on liquidity levels and historical routing performance, encouraging natural channel rebalancing through market-based incentives.

## Core Algorithm Overview

The system implements a sophisticated fee adjustment algorithm based on these principles:

- **Inverse Liquidity Pricing**: Fees decrease as outgoing liquidity increases (to attract routing) and increase as liquidity decreases (to preserve remaining capacity)
- **True Fee Calculation**: Accounts for inbound discounts when calculating historical performance, providing accurate fee analytics
- **EMA Smoothing**: Uses 14-day Exponential Moving Average (α=0.15) to smooth fee calculations and reduce volatility
- **Incremental Adjustments**: Moves fees gradually toward targets (10% per update) to avoid gossip spam and market shock

### Fee Calculation Formula
```
liquidity_ratio = local_balance / capacity
target_fee = avg_fee × 2 × (1 - liquidity_ratio)
new_fee = current_fee + 0.1 × (target_fee - current_fee)
```

At 0% liquidity: fees approach 2× average  
At 50% liquidity: fees approach 1× average  
At 100% liquidity: fees approach 0 ppm

## Features
- **True Fee Analytics**: Calculates actual fee performance accounting for inbound discounts and surcharges
- **EMA-Based Targeting**: Uses 14-day exponential moving average for stable, data-driven fee decisions
- **Liquidity-Responsive Pricing**: Automatically adjusts fees inversely to channel liquidity levels
- **Selective Channel Processing**: Configure specific channels via `CHAN_IDS` for testing or partial deployment
- **Minibolt Integration**: Designed specifically for Minibolt guide security and operational patterns
- **Batch Updates**: 30-minute intervals prevent gossip spam while maintaining responsiveness
- **Persistent State**: Maintains fee history and averages across restarts using SQLite and JSON persistence

## Prerequisites
- **LND**: Lightning Network Daemon v0.15+ (tested with v0.19.1), running and synced
- **Python**: Version 3.8+ (Ubuntu default on Minibolt)
- **charge-lnd**: Installed in a Python virtual environment for fee application
- **System Tools**: `sqlite3` for database operations, `jq` for JSON parsing (`sudo apt install jq sqlite3`)
- **Macaroon**: LND macaroon with appropriate permissions for channel policy management
- **Environment**: Designed for Minibolt guide implementations with `admin` user in `lnd` group

## Installation

### 1. Set up directories
```bash
mkdir -p ~/autofee ~/autofee/charge-lnd
```

### 2. Install charge-lnd
```bash
cd ~/autofee/charge-lnd
python3 -m venv venv
source venv/bin/activate
pip install charge-lnd
deactivate
```

### 3. Copy the script
- Place `autofee_wrapper.py` in `~/autofee/`
- Ensure executable permissions: `chmod +x ~/autofee/autofee_wrapper.py`

### 4. Create LND macaroon
Generate a macaroon with required permissions:
```bash
lncli bakemacaroon uri:/lnrpc.Lightning/GetInfo uri:/lnrpc.Lightning/ListChannels uri:/lnrpc.Lightning/ForwardingHistory uri:/lnrpc.Lightning/GetChanInfo uri:/lnrpc.Lightning/UpdateChannelPolicy > ~/autofee/charge-lnd.macaroon
```
Secure the macaroon:
```bash
chmod 600 ~/autofee/charge-lnd.macaroon
```

## Directory Structure
```
~/autofee/
├── autofee_wrapper.py        # Main script implementing the algorithm
├── autofee_wrapper.log       # Execution logs and debugging info
├── avg_fees.json            # Persisted EMA fees for stagnant periods
├── dynamic_charge.ini        # Generated charge-lnd configuration
├── fee_history.db           # SQLite database for forwarding history
├── charge-lnd.macaroon      # LND macaroon for API access
└── charge-lnd/              # charge-lnd installation
    └── venv/                # Isolated Python virtual environment
```

## How It Works

### Algorithm Flow

**1. Data Collection & Processing**
- Fetches 14 days of forwarding history from LND's `ForwardingHistory` API
- Calculates "true fees" by inferring inbound discounts: `true_fee = actual_fee + inferred_discount`
- Stores processed forwarding data in SQLite database for efficient querying

**2. True Fee Calculation**
The system accounts for inbound fee adjustments that affect net earnings:
```
expected_outbound_fee = (amount × current_fee_rate) + base_fee
inbound_discount = max(0, expected_outbound_fee - actual_fee_earned)
true_fee = actual_fee_earned + inbound_discount
```
This ensures EMA calculations reflect the gross outbound-equivalent fee for accurate performance analysis.

**3. EMA Fee Calculation**
- Computes Exponential Moving Average using α=0.15 over 14-day window
- Seeds initial EMA with first forward if no persisted value exists
- Maintains last calculated average during periods of no routing activity
- Enforces minimum of 10 ppm after calculation to prevent zero-fee scenarios

**4. Liquidity-Based Fee Targeting**
- Calculates liquidity ratio: `local_balance / capacity`
- Derives target fee using inverse relationship: `target = avg_fee × 2 × (1 - ratio)`
- Higher liquidity → lower fees (encourage routing)
- Lower liquidity → higher fees (preserve capacity)

**5. Incremental Adjustment**
- Standard adjustment: `new_fee = current + 0.1 × (target - current)`
- Special case for low fees: If both current ≤ 5 ppm and target ≤ 5 ppm, reduce by 1 ppm
- Ensures gradual transitions and prevents market disruption

**6. Configuration Generation & Application**
- Generates `dynamic_charge.ini` with per-channel static fee policies
- Uses decimal SCID format for charge-lnd channel matching
- charge-lnd applies fees via LND's `UpdateChannelPolicy` gRPC call

### Dual Persistence System
The system uses two storage mechanisms for different purposes:

- **SQLite Database (`fee_history.db`)**: Stores detailed forwarding history, timestamps, and true fee calculations for EMA computation
- **JSON File (`avg_fees.json`)**: Lightweight persistence of last calculated EMA values for periods without routing activity

This hybrid approach provides both detailed analytics and fast startup performance.

## Configuration

### Channel Selection
Edit `CHAN_IDS` in `autofee_wrapper.py` (around line 20) to process specific channels:

```python
# Process specific channels (use full channel IDs from lncli listchannels)
CHAN_IDS = [
    'f18d1930764e5577fd95e1283af3859bb24e95a87b07320ae44a815826123456',
    '3f476ef790e861f88bbfb75173695cb1106f0078fe7d13836c18db614587514a'
]

# Process all channels
CHAN_IDS = []
```

**Finding Channel IDs Using Alias:**
```bash
lncli listchannels | jq '.channels[] | select(.peer_alias == "<alias>") | {chan_id, channel_point, peer_alias, scid}'
```

### Testing Strategy
**⚠️ Always test on a single channel first:**
1. Choose a low-volume channel for initial testing
2. Set `CHAN_IDS = ['your_test_channel_id']`
3. Run manually with `--dry-run` to verify calculations
4. Monitor for several intervals before expanding to more channels

## Operating the Program

### Manual Execution
**Test run (no changes applied):**
```bash
~/autofee/autofee_wrapper.py && sleep 10 && cd ~/autofee/charge-lnd && source venv/bin/activate && charge-lnd --macaroon ~/autofee/charge-lnd.macaroon -c ~/autofee/dynamic_charge.ini --dry-run -v && deactivate
```

**Live run (applies fee changes):**
```bash
~/autofee/autofee_wrapper.py && sleep 10 && cd ~/autofee/charge-lnd && source venv/bin/activate && charge-lnd --macaroon ~/autofee/charge-lnd.macaroon -c ~/autofee/dynamic_charge.ini -v && deactivate
```

**Monitor logs:**
```bash
tail -f ~/autofee/autofee_wrapper.log
```

### Automated Operation (Cron)
**Setup 30-minute automation:**
```bash
crontab -e
```

Add this line (adjust paths for your username):
```bash
SHELL=/bin/bash
*/30 * * * * /home/admin/autofee/autofee_wrapper.py && sleep 10 && cd /home/admin/autofee/charge-lnd && source venv/bin/activate && charge-lnd --macaroon /home/admin/autofee/charge-lnd.macaroon -c /home/admin/autofee/dynamic_charge.ini -v >> /home/admin/autofee/cron.log 2>&1 && deactivate
```

**Monitor cron execution:**
```bash
tail -f ~/autofee/cron.log
```

### Understanding the Output

**Example log output:**
```
2025-07-20 05:47:54,848 INFO: Channel 3f476ef790e861f88bbfb75173695cb1106f0078fe7d13836c18db614712345a: avg_fee=620, ratio=0.70, current=620, target=366, new=595
2025-07-20 05:47:54,899 INFO: Channel f18d1930764e5577fd95e1283af3859bb24e95a87b07320ae44a815812345675: avg_fee=121, ratio=0.70, current=30, target=73, new=34
```

**Reading the values:**
- `avg_fee=620`: 14-day EMA of true fees (620 ppm)
- `ratio=0.70`: 70% outbound liquidity
- `current=620`: Current channel fee
- `target=366`: Calculated target fee based on liquidity
- `new=595`: New fee after 10% increment toward target

## Monitoring and Verification

### Verify Fee Changes
Check applied fees using LND directly:
```bash
# Get channel info (use SCID from listchannels)
lncli getchaninfo <short_channel_id>

# Check your node's policy (node1 or node2)
lncli getchaninfo <scid> | jq '.node1_policy.fee_rate_milli_msat'
```

### Monitor Channel Performance
```bash
# View recent forwards for a channel
lncli fwdinghistory --start_time $(date -d '7 days ago' +%s) | jq '.forwarding_events[] | select(.chan_id_out=="<scid>")'

# Check current channel balances
lncli listchannels | jq '.channels[] | {chan_id, local_balance, capacity}'
```

### Expected Behavior Examples

**High Liquidity Channel (80% outbound):**
- Target fee: `avg_fee × 2 × (1 - 0.8) = avg_fee × 0.4`
- Result: Lower fees to encourage routing and balance the channel

**Low Liquidity Channel (20% outbound):**
- Target fee: `avg_fee × 2 × (1 - 0.2) = avg_fee × 1.6`
- Result: Higher fees to preserve remaining capacity

**Balanced Channel (50% outbound):**
- Target fee: `avg_fee × 2 × (1 - 0.5) = avg_fee × 1.0`
- Result: Fees approach historical average

## Troubleshooting

### Common Issues

**"lncli command failed" errors:**
- Verify LND is running and synced: `lncli getinfo`
- Check macaroon permissions and file access
- Ensure `/data/lnd` access if using Minibolt setup

**"No policy found for channel" warnings:**
- Channel may be private or recently closed
- Verify channel exists: `lncli listchannels | grep <chan_id>`

**charge-lnd errors:**
- Activate virtual environment before running
- Verify macaroon includes `UpdateChannelPolicy` permission
- Check `dynamic_charge.ini` syntax

**Database errors:**
- Ensure SQLite3 is installed: `sqlite3 --version`
- Check file permissions on `~/autofee/` directory
- Database auto-creates on first run

### Log Analysis
**Normal operation indicators:**
- "Generated INI for X channels" messages
- Channel-specific fee calculations with reasonable values
- No error messages in charge-lnd output

**Warning signs:**
- Repeated "Error calculating avg fee" messages
- All channels showing same fees (possible configuration issue)
- charge-lnd "no matching channels" warnings

## Integration with charge-lnd

This system leverages [charge-lnd](https://github.com/accumulator/charge-lnd) for the actual fee application:

- **Why charge-lnd**: Proven, stable tool for LND policy management with excellent configuration flexibility
- **Integration approach**: Generate dynamic configuration files that charge-lnd consumes
- **Channel matching**: Uses decimal SCID format for precise channel identification
- **Batch application**: Single gRPC call applies all fee changes efficiently

The generated `dynamic_charge.ini` follows charge-lnd's configuration format:
```ini
[autofee-992805x123x1]
chan.id = 992805123934453761
strategy = static
fee_ppm = 150
```

## Security and Permissions

### Macaroon Security
- Store macaroon with restrictive permissions (600)
- Use least-privilege principle - only include required gRPC methods
- Regularly rotate macaroons for production deployments

### File Permissions
- Script: `755` (executable by admin)
- Macaroon: `600` (read-only by admin)
- Database and logs: `644` (readable by admin/lnd group)

### Minibolt Compatibility
- Designed for `admin` user in `lnd` group
- Uses `/data/lnd` access patterns per Minibolt guide
- Logs to user directory (not system logs)
- No sudo requirements for normal operation

## Limitations and Considerations

### Current Limitations
- **Single Direction**: Only manages outbound fees (inbound fee automation planned for future versions)

### Operational Considerations
- **Gossip Impact**: 30-minute update intervals balance responsiveness with network courtesy
- **Market Response**: Gradual fee adjustments (10% increments) prevent rapid market changes
