# Autofee - Intelligent Lightning Network Fee Management

A comprehensive, data-driven fee automation system for Lightning Network node operators. Autofee uses Exponential Moving Averages (EMA) of actual routing fees to dynamically adjust channel fees based on liquidity, routing history, and channel behavior.

## What It Does

Autofee provides sophisticated fee management through multiple coordinated components:

### Core Features

**1. Intelligent Outbound Fee Management**
- Calculates optimal fees using EMA of actual routing fees (true fees that account for inbound discounts)
- Adjusts fees based on channel liquidity with configurable pivot points
- Gradually moves fees toward targets to avoid sudden changes
- Maintains 14-day rolling history in SQLite database

**2. Negative Inbound Fee Management**
- Automatically applies negative inbound fees (discounts) to attract liquidity
- Triggers when channels drop below configurable thresholds (default: 20% liquidity)
- Incrementally increases discounts until liquidity improves
- Removes discounts when liquidity recovers (above 40% by default)
- Checks remote peer fees to avoid wasting discounts on high-fee channels

**3. Stagnant Channel Detection**
- Identifies channels with no routing activity for configurable periods (default: 24 hours)
- Reduces fees on stagnant channels to encourage routing
- Automatically restores fees when routing resumes
- Uses actual forwarding history from database, not just liquidity changes

**4. Max HTLC Optimization**
- Sets maximum HTLC size to 98% of usable balance (after 1% reserve)
- Updates automatically based on current liquidity
- Prevents channels from being excluded from large payment attempts

**5. Advanced Optional Features**
- **Custom Pivot Points**: Set specific liquidity targets where average fee is centered
- **Channel Groups**: Synchronize fees across multiple channels (e.g., to same peer)
- **Minimum Fee Enforcement**: Set floor fees using static values or average fees

### How It Works

**Fee Calculation Philosophy:**

The system uses a sophisticated approach to fee management:

1. **True Fee Tracking**: When a channel has a negative inbound fee, the `fee_msat` in forwarding history represents the net fee earned (outbound fee minus inbound discount). Autofee calculates the "true fee" by inferring the inbound discount and adding it back, giving you the gross outbound-equivalent fee for accurate EMA calculations.

2. **EMA Convergence**: Instead of chasing every routing event, fees converge toward the EMA target using a configurable adjustment factor (default: 5%). This creates smooth, gradual fee changes.

3. **Liquidity-Based Curves**: Fees scale linearly based on liquidity:
   - High liquidity (90%+): Fees approach 0 to encourage outbound routing
   - Low liquidity (10%-): Fees increase to attract inbound routing
   - Balanced (50%): Fees equal the EMA average

4. **State Persistence**: All state is preserved across runs:
   - `avg_fees.json`: EMA values per channel
   - `neginb_fees.json`: Negative inbound fee state
   - `stagnant_state.json`: Stagnant channel tracking
   - `fee_history.db`: SQLite database of all forwards

## Installation

### Prerequisites

- **LND Node**: v0.18.0 or higher (required for inbound fees capability)
- **Python 3**: Version 3.7 or higher
- **charge-lnd**: Required for applying fee policies
- **lncli**: Must be in PATH and functional
- **git**: For cloning the repository

### Step 1: Create Directory Structure

```bash
mkdir -p ~/autofee
cd ~/autofee
```

### Step 2: Install charge-lnd

```bash
# Clone charge-lnd
git clone https://github.com/accumulator/charge-lnd.git
cd charge-lnd

# Create virtual environment and install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
deactivate

cd ~/autofee
```

### Step 3: Create LND Macaroon

Create a restricted macaroon with minimal required permissions:

```bash
lncli bakemacaroon \
  info:read \
  offchain:read \
  offchain:write \
  --save_to ~/autofee/charge-lnd.macaroon
```

**Permissions explained:**
- `info:read`: Get node identity and basic info
- `offchain:read`: Read channel data and forwarding history
- `offchain:write`: Update channel policies

### Step 4: Clone Autofee Repository

```bash
cd ~/autofee

# Clone the repository
git clone https://github.com/Benny-444/autofee.git autofee-scripts

# Copy scripts to the main autofee directory
cp autofee-scripts/*.py .
cp autofee-scripts/*.sh .

# Make all scripts executable
chmod +x *.py *.sh

# Optionally remove the cloned directory (or keep it for easy updates)
rm -rf autofee-scripts
```

### Step 5: Initialize Database

The database is created automatically on first run, but you can initialize it manually:

```bash
~/autofee/autofee_wrapper.py
```

This creates:
- `fee_history.db`: SQLite database for forwarding history
- `avg_fees.json`: Initial EMA values based on current fees

### Step 6: Verify Installation

Test each component:

```bash
# Test outbound fee calculation
~/autofee/autofee_wrapper.py
cat ~/autofee/dynamic_charge.ini

# Test inbound fees
~/autofee/autofee_neginb_wrapper.py

# Test stagnant detection
~/autofee/autofee_stagnant_wrapper.py

# Test max HTLC
~/autofee/autofee_maxhtlc_wrapper.py

# Dry-run with charge-lnd
cd ~/autofee/charge-lnd
source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini --dry-run -v
deactivate
```

## Configuration

### Core Parameters

Edit the configuration constants at the top of each script:

#### `autofee_wrapper.py` (Outbound Fees)

```python
ALPHA = 0.15                    # EMA smoothing factor (0.05-0.3)
MIN_AVG_FEE = 10                # Minimum average fee (ppm)
DAYS_BACK = 14                  # Days of history to maintain
ADJUSTMENT_FACTOR = 0.05        # % of difference to apply per run
CHAN_IDS = []                   # Empty = all channels
EXCLUDE_CHAN_IDS = []           # Channels to skip
```

**Tuning ALPHA:**
- Lower (0.05-0.10): Slower convergence, more stable
- Medium (0.15-0.20): Balanced responsiveness
- Higher (0.25-0.30): Fast adaptation to changes

**Tuning ADJUSTMENT_FACTOR:**
- Lower (0.01-0.05): Gradual fee changes
- Higher (0.10-0.20): Faster convergence

#### `autofee_neginb_wrapper.py` (Negative Inbound Fees)

```python
NEGATIVE_INBOUND_TRIGGER = 20   # Apply when below this % (default: 20%)
NEGATIVE_INBOUND_REMOVE = 40    # Remove when above this % (default: 40%)
MAX_REMOTE_FEE_FOR_INBOUND = 2  # Max remote fee (ppm) to qualify
EXCLUDE_REMOTE_FEE_CHECK = []   # Channels to skip remote fee check
INITIAL_INBOUND_PCT = 30        # Initial discount (% of avg_fee)
INCREMENT_PCT = 1               # Increment per run (% of avg_fee)
MAX_INBOUND_PCT = 70            # Maximum discount (% of avg_fee)
```

**Example Calculation:**
- Channel with avg_fee = 100 ppm drops to 15% liquidity
- First run: Sets inbound_fee = -30 ppm (30% of 100)
- After 30 minutes: Sets inbound_fee = -31 ppm
- Continues incrementing until liquidity recovers or max reached (-70 ppm)

#### `autofee_stagnant_wrapper.py` (Stagnant Channels)

```python
STAGNANT_RATIO_THRESHOLD = 0.20  # Must be above 20% liquidity
STAGNANT_HOURS = 24              # Hours without routing
STAGNANT_REDUCTION_PCT = 0.5     # Reduce fees by 0.5% each run
```

**How it works:**
- Channels above 20% liquidity with no forwards for 24+ hours are "stagnant"
- Fees reduced by 0.5% every 30 minutes until routing resumes
- Uses actual forwarding history from database, not liquidity changes

#### `autofee_maxhtlc_wrapper.py` (Max HTLC)

```python
MAX_HTLC_RATIO = 0.98    # 98% of usable balance
RESERVE_OFFSET = 0.01    # 1% reserve (unusable by protocol)
```

**Calculation:**
- Usable balance = local_balance - (capacity × 0.01)
- Max HTLC = usable_balance × 0.98
- Special case: 0 balance channels get 1 sat max HTLC

### Channel Filtering

All scripts support channel filtering:

```python
# Process only specific channels
CHAN_IDS = ['12345678901234567890', '09876543210987654321']

# Exclude specific channels
EXCLUDE_CHAN_IDS = ['11111111111111111111']
```

Use SCID (short channel ID) format - the decimal number, not the x format.

### Optional Features

#### Custom Pivot Points (`autofee_pivot_wrapper.py`)

For channels where you want the average fee centered at a different liquidity point:

```python
AVG_FEE_PIVOT = 0.6              # Center avg fee at 60% liquidity
ADJUSTMENT_FACTOR = 0.05
CHAN_IDS = ['12345678901234567890']  # REQUIRED: Specify channels
```

**Examples:**
- Pivot = 0.5 (50%): Standard behavior, avg_fee at 50% liquidity
- Pivot = 0.6 (60%): Protects against low liquidity more aggressively
- Pivot = 0.4 (40%): Encourages outflow even at moderate liquidity

**Multiple Channels with Different Pivots:**

Since each channel may benefit from a different pivot strategy, you can create multiple copies of the script:

```bash
# In ~/autofee/
cp autofee_pivot_wrapper.py autofee_pivot_channel1.py
cp autofee_pivot_wrapper.py autofee_pivot_channel2.py
cp autofee_pivot_wrapper.py autofee_pivot_channel3.py

# Make executable
chmod +x autofee_pivot_*.py
```

Then configure each copy individually:

```python
# autofee_pivot_channel1.py - Conservative peer
AVG_FEE_PIVOT = 0.6              # Protect against depleting
CHAN_IDS = ['111111111111111111']

# autofee_pivot_channel2.py - Encourage outflow
AVG_FEE_PIVOT = 0.4              # Push liquidity out
CHAN_IDS = ['222222222222222222']

# autofee_pivot_channel3.py - Aggressive protection
AVG_FEE_PIVOT = 0.7              # Strongly discourage depletion
CHAN_IDS = ['333333333333333333']
```

Add each to `run_autofee.sh` in the optional scripts section (Step 6), before charge-lnd runs:

```bash
# Optional Scripts - Custom pivots for specific channels
~/autofee/autofee_pivot_channel1.py
sleep 1
~/autofee/autofee_pivot_channel2.py
sleep 1
~/autofee/autofee_pivot_channel3.py
sleep 1

# Then apply with charge-lnd...
```

Each script will override the fees for its specified channels, applying its unique pivot strategy.

#### Channel Groups (`autofee_group_wrapper.py`)

Synchronize fees across multiple channels:

```python
CHANNEL_GROUPS = [
    {
        'name': 'ACINQ_channels',
        'chan_ids': ['111111111', '222222222'],
        'strategy': 'highest',        # or 'lowest', 'average', 'static'
        'sync_inbound': True,
        'inbound_strategy': 'lowest',
        'enabled': True
    }
]
```

**Strategies:**
- `highest`: Use highest fee from group
- `lowest`: Use lowest fee from group
- `average`: Use average of all fees
- `static`: Use fixed value

#### Minimum Fee Enforcement (`autofee_minfee_wrapper.py`)

Ensure channels don't go below minimums - useful for preventing race-to-the-bottom fee competition:

```python
CHANNEL_MINIMUMS = [
    # Example 1: Static minimum fee
    {
        'chan_id': '12345678901234567890',
        'min_type': 'static',     # Use a fixed minimum
        'min_value': 100,         # Minimum 100 ppm
        'enabled': True
    },
    
    # Example 2: Use full average fee as minimum (100%)
    {
        'chan_id': '09876543210987654321',
        'min_type': 'avg_fee',    # Use the channel's avg_fee
        'enabled': True
    },
    
    # Example 3: Use 80% of average fee as minimum
    {
        'chan_id': '11111111111111111111',
        'min_type': 'avg_fee',
        'avg_fee_percentage': 0.8,  # 80% of avg_fee
        'enabled': True
    },
    
    # Example 4: Use 120% of average fee as minimum
    {
        'chan_id': '22222222222222222222',
        'min_type': 'avg_fee',
        'avg_fee_percentage': 1.2,  # 120% of avg_fee
        'enabled': True
    },
]
```

**How it works:**

- **Static minimums**: Simple floor value - fees never go below this number
- **avg_fee minimums**: Uses the channel's current EMA from `avg_fees.json`
  - Default (no percentage specified): Uses 100% of avg_fee
  - With percentage: Calculates `avg_fee × percentage`
  - Example: If avg_fee = 150 ppm and percentage = 0.8, minimum = 120 ppm

**Use cases:**

- **Static**: Good for new channels without routing history
- **100% avg_fee**: Prevents fees from dropping below historical average
- **<100% (e.g., 0.8)**: Allows some flexibility while maintaining a floor
- **>100% (e.g., 1.2)**: More aggressive minimum, keeps fees elevated

**Note:** This script only *raises* fees to meet minimums - it never lowers them.

## Usage

### Manual Execution

Run individual components:

```bash
# Update outbound fees
~/autofee/autofee_wrapper.py

# Update inbound fees
~/autofee/autofee_neginb_wrapper.py

# Check for stagnant channels
~/autofee/autofee_stagnant_wrapper.py

# Optimize max HTLC
~/autofee/autofee_maxhtlc_wrapper.py

# Apply all fees with charge-lnd
cd ~/autofee/charge-lnd
source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini -v
deactivate
```

### Automated Execution (Recommended)

The `run_autofee.sh` script orchestrates all components in the correct order:

```bash
~/autofee/run_autofee.sh
```

#### Critical Execution Order

The scripts **must** run in this specific order for the system to work correctly:

**1. Log Trimmer** (optional, not critical)
```bash
autofee_log_trimmer.py
```
- Keeps logs under control (50k lines per file)
- Safe to skip if it fails

**2. Outbound Fee Wrapper** (REQUIRED - must be first)
```bash
autofee_wrapper.py
```
- **Why first:** Generates the base `dynamic_charge.ini` file
- Updates `fee_history.db` with new forwarding events
- Calculates EMA and updates `avg_fees.json`
- Creates INI sections for all channels with outbound fees
- **Critical:** All other scripts modify this INI - it must exist first
- **If this fails:** Stop - other scripts have nothing to work with

**3. Inbound Fee Wrapper** (modifies INI)
```bash
autofee_neginb_wrapper.py
```
- **Why after wrapper:** Reads `avg_fees.json` created by wrapper
- **What it does:** Opens existing INI, adds `inbound_fee_ppm` to sections
- Uses configparser to read → modify → write INI
- **If this fails:** Continue - outbound fees still work

**4. Stagnant Detection** (modifies INI)
```bash
autofee_stagnant_wrapper.py
```
- **Why after wrapper:** Checks `fee_history.db` for recent forwards
- **Why after inbound:** Can reduce both outbound AND inbound fees
- **What it does:** Opens INI, reduces fees for stagnant channels
- Overrides fees set by wrapper/neginb for stagnant channels
- **If this fails:** Continue - regular fees still applied

**5. Max HTLC Optimizer** (modifies INI)
```bash
autofee_maxhtlc_wrapper.py
```
- **Why after wrapper:** Needs INI sections to exist
- **What it does:** Opens INI, adds `max_htlc_msat` to each section
- Independent of other fee calculations (separate field)
- **If this fails:** Continue - fees work without max HTLC

**6. Optional Scripts** (final overrides - if configured)

These are commented out by default but can be enabled:

```bash
# autofee_pivot_wrapper.py    # Custom pivot points for specific channels
# autofee_group_wrapper.py    # Synchronize fees across channel groups
# autofee_minfee_wrapper.py   # Enforce minimum fee floors
```

- **Why last:** These are final overrides for specific channels
- Must come after all standard fee calculations
- Only run if configured (have specific channels in CHAN_IDS)

**7. Apply Changes with charge-lnd** (MUST BE LAST)
```bash
cd ~/autofee/charge-lnd
source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini -v
deactivate
```
- **Why last:** Applies the fully-constructed INI to your node
- All modifications must be complete before this runs
- **If this fails:** Check INI syntax and charge-lnd logs

#### Why This Order Matters

The system uses a **pipeline architecture**:

```
1. GENERATE base INI
   └─> autofee_wrapper.py creates dynamic_charge.ini

2. MODIFY/ENHANCE INI  
   ├─> autofee_neginb_wrapper.py adds inbound_fee_ppm
   ├─> autofee_stagnant_wrapper.py reduces fees (overrides)
   ├─> autofee_maxhtlc_wrapper.py adds max_htlc_msat
   └─> optional scripts make final overrides

3. APPLY INI to node
   └─> charge-lnd applies all changes at once
```

**Key Principles:**

1. **Generator must be first** - `autofee_wrapper.py` creates the INI structure
2. **Modifiers work on existing INI** - All other scripts use configparser to read, modify, and write back
3. **Later scripts override earlier ones** - Stagnant can override wrapper's fees
4. **Application happens once at the end** - charge-lnd sees the final result

**What happens if you run out of order:**

- Run neginb before wrapper: ❌ No INI exists to modify
- Run stagnant before wrapper: ❌ No fee_history.db to check
- Run charge-lnd in the middle: ❌ Applies incomplete configuration
- Skip wrapper entirely: ❌ Nothing works - no INI generated

#### File System Synchronization

Note the `sleep 1` commands between scripts - these ensure:
- File writes complete before next script reads
- Database commits are finished
- No race conditions on slower storage

On fast SSDs these may be unnecessary, but they're harmless and provide safety on slower systems.

### Cron Setup

Add to your crontab for automated execution:

```bash
crontab -e
```

**Recommended: Every 30 minutes**
```
*/30 * * * * /home/admin/autofee/run_autofee.sh
```

**Alternative: Every hour**
```
0 * * * * /home/admin/autofee/run_autofee.sh
```

**Conservative: Every 4 hours**
```
0 */4 * * * /home/admin/autofee/run_autofee.sh
```

The script logs all activity to `~/autofee/cron.log`.

### Monitoring

#### View Current State

```bash
# Show average fees and routing counts
~/autofee/show_avgfee.sh

# Show recent autofee adjustments
~/autofee/show_autofee.sh

# Show negative inbound fee state
~/autofee/show_neginb.sh

# Show stagnant channel state
~/autofee/show_stagnant_state.sh
```

#### Check Logs

```bash
# Main execution log
tail -f ~/autofee/cron.log

# Individual component logs
tail -f ~/autofee/autofee_wrapper.log
tail -f ~/autofee/autofee_neginb_wrapper.log
tail -f ~/autofee/autofee_stagnant_wrapper.log
tail -f ~/autofee/autofee_maxhtlc_wrapper.log
```

### Maintenance

#### Reset Channel Average Fee

Use when you want to restart the EMA calculation:

```bash
# Reset specific channel to new starting fee
~/autofee/reset_avgfee.sh 150 12345678901234567890

# This will:
# - Delete all routing history for that channel
# - Set avg_fee to the specified value (150 ppm)
# - Create a backup of avg_fees.json
```

#### Reset Inbound Fee State

Reset a channel's negative inbound fee:

```bash
# Reset to specific inbound fee
~/autofee/reset_neginb.sh -50 12345678901234567890

# Remove inbound fee completely
~/autofee/reset_neginb.sh 0 12345678901234567890
```

#### Reset Max HTLC Values

Bulk reset all channels to 99% of capacity:

```bash
# Dry run first
~/autofee/reset_max_htlc.py --dry-run

# Actually reset
~/autofee/reset_max_htlc.py
```

#### Trim Logs

Logs are automatically trimmed by `run_autofee.sh`, but you can manually trim:

```bash
~/autofee/autofee_log_trimmer.py
```

Keeps last 50,000 lines per log file (approximately 5-10MB).

## Understanding the System

### Fee Calculation Details

**Standard Fee Curve (50% pivot):**
```
fee = avg_fee × 2 × (1 - liquidity_ratio)

Examples with avg_fee = 100 ppm:
- 100% liquidity (1.0): fee = 100 × 2 × (1 - 1.0) = 0 ppm
- 50% liquidity (0.5):  fee = 100 × 2 × (1 - 0.5) = 100 ppm
- 0% liquidity (0.0):   fee = 100 × 2 × (1 - 0.0) = 200 ppm
```

**Adjustment Application:**
```
target_fee = 100 ppm
current_fee = 80 ppm
difference = 20 ppm
adjustment = 20 × 0.05 = 1 ppm (minimum)
new_fee = 80 + 1 = 81 ppm
```

Fees gradually converge toward the target over multiple runs.

### True Fee Calculation

When a forward occurs with an inbound discount:

```
Outbound fee policy: 100 ppm
Inbound fee policy: -30 ppm
Amount: 1,000,000 msat

Expected outbound fee: 1,000,000 × 100 / 1,000,000 = 100 msat
Actual fee earned (fee_msat): 70 msat (100 - 30)

True fee calculation:
- Inferred discount: 100 - 70 = 30 msat
- True fee: 70 + 30 = 100 msat
- True fee ppm: 100 msat / 1,000,000 msat × 1,000,000 = 100 ppm
```

This true fee (100 ppm) is stored in the database and used for EMA calculations, ensuring inbound discounts don't artificially lower your average fees.

### EMA Calculation

```python
# First forward for channel (no prior EMA)
ema = true_fee_ppm

# Subsequent forwards
ema = ALPHA × true_fee_ppm + (1 - ALPHA) × previous_ema

# With ALPHA = 0.15:
ema = 0.15 × true_fee_ppm + 0.85 × previous_ema
```

**Example convergence (ALPHA = 0.15, starting ema = 100):**
- Forward with 150 ppm: ema = 0.15 × 150 + 0.85 × 100 = 107.5
- Forward with 150 ppm: ema = 0.15 × 150 + 0.85 × 107.5 = 113.9
- Forward with 150 ppm: ema = 0.15 × 150 + 0.85 × 113.9 = 119.3
- Continues converging toward 150...

### Stagnant Detection Logic

A channel becomes stagnant when:
1. Current liquidity > 20% (has outbound capacity)
2. AND no forwards in last 24 hours (configurable)

**Uses actual forwarding history:**
```sql
SELECT MAX(timestamp) FROM fee_history WHERE chan_id = ?
```

Not just liquidity changes - must have actual routing activity to clear stagnant status.

### Negative Inbound Fee Lifecycle

**Phase 1: Initialization**
- Channel drops below 20% liquidity
- Channel has previously been above 20% (prevents new channels from triggering)
- Remote peer's fee ≤ 2 ppm (configurable check)
- Sets initial discount: -30% of avg_fee

**Phase 2: Incrementation**
- Every 30 minutes (or your cron interval)
- Increases discount by 1% of avg_fee
- Continues until MAX_INBOUND_PCT (70%) or liquidity recovers

**Phase 3: Maintenance**
- Channel between 20-40% liquidity
- Maintains current discount percentage
- Adjusts absolute value if avg_fee changes

**Phase 4: Removal**
- Channel rises above 40% liquidity
- Discount removed entirely
- Channel can re-trigger if it drops below 20% again

## File Structure

```
~/autofee/
├── Core Scripts
│   ├── autofee_wrapper.py           # Outbound fee management (EMA)
│   ├── autofee_neginb_wrapper.py    # Negative inbound fee manager
│   ├── autofee_stagnant_wrapper.py  # Stagnant channel detector
│   ├── autofee_maxhtlc_wrapper.py   # Max HTLC optimizer
│   └── run_autofee.sh               # Main orchestration script
│
├── Optional Scripts
│   ├── autofee_pivot_wrapper.py     # Custom pivot points
│   ├── autofee_group_wrapper.py     # Channel group sync
│   └── autofee_minfee_wrapper.py    # Minimum fee enforcement
│
├── Reporting & Utilities
│   ├── autofee_log_trimmer.py       # Log management
│   ├── reset_max_htlc.py            # Bulk HTLC reset
│   ├── reset_avgfee.sh              # Reset channel EMA
│   ├── reset_neginb.sh              # Reset inbound fee
│   └── show_*.sh                    # Monitoring scripts
│
├── State Files
│   ├── avg_fees.json                # EMA values per channel
│   ├── neginb_fees.json             # Inbound fee state
│   ├── stagnant_state.json          # Stagnant tracking
│   └── fee_history.db               # SQLite forwarding history
│
├── Generated Files
│   ├── dynamic_charge.ini           # charge-lnd config
│   └── *.log                        # Component logs
│
└── charge-lnd/                      # charge-lnd installation
    └── venv/                        # Python virtual environment
```

## Troubleshooting

### Script Fails to Run

**Check Python version:**
```bash
python3 --version  # Need 3.7+
```

**Check lncli access:**
```bash
lncli getinfo
```

**Check macaroon permissions:**
```bash
lncli printmacaroon --macaroon_file ~/autofee/charge-lnd.macaroon
```

### No Fees Being Updated

**Check if INI is generated:**
```bash
cat ~/autofee/dynamic_charge.ini
```

**Check charge-lnd execution:**
```bash
cd ~/autofee/charge-lnd
source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini --dry-run -v
```

**Check cron execution:**
```bash
tail -100 ~/autofee/cron.log
```

### Database Errors

**Rebuild database:**
```bash
# Backup existing
cp ~/autofee/fee_history.db ~/autofee/fee_history.db.backup

# Remove and reinitialize
rm ~/autofee/fee_history.db
~/autofee/autofee_wrapper.py
```

### Excessive Fee Changes

**Increase ADJUSTMENT_FACTOR:**
```python
# In autofee_wrapper.py
ADJUSTMENT_FACTOR = 0.02  # Slower changes (2% instead of 5%)
```

**Increase ALPHA for more stable EMA:**
```python
# In autofee_wrapper.py
ALPHA = 0.10  # More stability (was 0.15)
```

### Channels Stuck Stagnant

**Check actual forwarding history:**
```bash
sqlite3 ~/autofee/fee_history.db \
  "SELECT COUNT(*), MAX(timestamp) FROM fee_history WHERE chan_id='your_scid';"
```

**Manually clear stagnant status:**
Edit `~/autofee/stagnant_state.json` and set `is_stagnant: false` for the channel.

## Advanced Topics

### Multiple Configurations

Run different configurations for different channel sets:

```bash
# Create separate directories
mkdir ~/autofee/aggressive
mkdir ~/autofee/conservative

# Copy and modify scripts
cp autofee_wrapper.py ~/autofee/aggressive/
# Edit aggressive version with higher ADJUSTMENT_FACTOR

# Separate cron entries
*/30 * * * * /home/admin/autofee/aggressive/run_autofee.sh
0 */4 * * * /home/admin/autofee/conservative/run_autofee.sh
```

### Testing New Parameters

Always test with dry-run:

```bash
# Modify configuration in script
vim ~/autofee/autofee_wrapper.py

# Run once manually
~/autofee/autofee_wrapper.py

# Check generated INI
cat ~/autofee/dynamic_charge.ini

# Dry-run with charge-lnd
cd ~/autofee/charge-lnd && source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini --dry-run -v
```

### Backup Strategy

```bash
# Backup state files before major changes
cd ~/autofee
tar -czf backup_$(date +%Y%m%d).tar.gz \
  avg_fees.json \
  neginb_fees.json \
  stagnant_state.json \
  fee_history.db
```

## Acknowledgments

- Built on charge-lnd by accumulator
- Inspired by LNDg's fee management approach
- Developed with Grok (xAI) and Claude (Anthropic)

## Support

For issues, questions, or discussion:
- Check logs first: `~/autofee/*.log`
- Review this README thoroughly
- Test components individually before reporting issues

---

**⚠️ Important Notes:**

- Always test new configurations with `--dry-run` first
- Monitor initial behavior closely after deployment
- Keep backups of state files before major changes
- This system makes real changes to your node - understand what it does before enabling automation

**🎯 Recommended Starting Configuration:**

For first deployment, use conservative settings:
```python
ALPHA = 0.15
ADJUSTMENT_FACTOR = 0.03
STAGNANT_HOURS = 48
NEGATIVE_INBOUND_TRIGGER = 15
```

Run manually for several days, monitor results, then enable cron automation once comfortable.