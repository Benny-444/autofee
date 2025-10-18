# Autofee Quickstart Guide

Get Autofee running in 15 minutes with sensible defaults.

## Prerequisites Check

```bash
# Check LND version (need v0.18.0+)
lncli getinfo | grep version

# Check Python version (need 3.7+)
python3 --version

# Verify lncli works
lncli getinfo
```

If any checks fail, install/upgrade the required software first.

## Installation (5 minutes)

### 1. Create Directory and Install charge-lnd

```bash
# Create main directory
mkdir -p ~/autofee
cd ~/autofee

# Clone and install charge-lnd
git clone https://github.com/accumulator/charge-lnd.git
cd charge-lnd
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .
deactivate
cd ~/autofee
```

### 2. Create Macaroon

```bash
lncli bakemacaroon \
  info:read \
  offchain:read \
  offchain:write \
  --save_to ~/autofee/charge-lnd.macaroon
```

### 3. Clone Autofee Scripts

```bash
cd ~/autofee
git clone https://github.com/Benny-444/autofee.git autofee-scripts
cp autofee-scripts/*.py .
cp autofee-scripts/*.sh .
chmod +x *.py *.sh
rm -rf autofee-scripts
```

## First Run (5 minutes)

### 1. Initialize Database and Generate Fees

```bash
# This creates the database and sets initial fees based on your current fees
~/autofee/autofee_wrapper.py

# Check that it worked
ls -lh ~/autofee/fee_history.db
cat ~/autofee/avg_fees.json
```

### 2. Test with charge-lnd (Dry Run)

```bash
cd ~/autofee/charge-lnd
source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini --dry-run -v
deactivate
```

**Look for:** "Updating X channels" and no errors.

### 3. Apply Fees (First Real Run)

```bash
~/autofee/run_autofee.sh

# Check the log
tail -50 ~/autofee/cron.log
```

**Look for:** "Autofee run completed" and no errors.

## Verify It's Working (2 minutes)

```bash
# Check channel fees were updated
lncli listchannels | grep -A 5 "fee_rate"

# View autofee state
~/autofee/show_avgfee.sh

# Check logs
tail ~/autofee/autofee_wrapper.log
```

## Enable Automation (1 minute)

```bash
# Edit crontab
crontab -e

# Add this line (runs every 30 minutes)
*/30 * * * * /home/admin/autofee/run_autofee.sh

# Save and exit
```

**Note:** Replace `/home/admin` with your actual home directory if different.

## Monitor (Ongoing)

```bash
# View current state
~/autofee/show_avgfee.sh           # Average fees and routing counts
~/autofee/show_autofee.sh          # Recent fee adjustments
~/autofee/show_neginb.sh           # Negative inbound fee status
~/autofee/show_stagnant_state.sh   # Stagnant channel tracking

# Check logs
tail -f ~/autofee/cron.log         # Main execution log
tail -f ~/autofee/autofee_wrapper.log  # Fee calculation details
```

## What Happens Now?

With default settings:

**Every 30 minutes, the system:**
1. Fetches new forwarding events since last run
2. Updates EMA (average fee) for each channel based on actual routing fees
3. Calculates target fees based on liquidity (more liquid = lower fees)
4. Gradually adjusts fees toward targets (5% per run by default)
5. Applies negative inbound fees to channels below 20% liquidity
6. Reduces fees on channels with no routing for 24+ hours
7. Optimizes max HTLC to 98% of usable balance
8. Applies all changes to your node via charge-lnd

**Your channels will:**
- Automatically balance fees based on liquidity
- Attract inbound liquidity when low
- Reduce fees when stagnant to encourage routing
- Converge toward routing-history-based averages over time

## Next Steps

### Review Settings (Optional but Recommended)

After running for a day or two, you may want to tune the settings:

```bash
# Edit main configuration
nano ~/autofee/autofee_wrapper.py

# Key settings to consider:
# ALPHA = 0.15              # How fast EMA adapts (0.10-0.20)
# ADJUSTMENT_FACTOR = 0.05  # How fast fees change (0.03-0.10)

# Edit inbound fee settings
nano ~/autofee/autofee_neginb_wrapper.py

# Key settings:
# NEGATIVE_INBOUND_TRIGGER = 20  # Apply inbound discount below this %
# NEGATIVE_INBOUND_REMOVE = 40   # Remove discount above this %
# MAX_REMOTE_FEE_FOR_INBOUND = 2 # Skip if peer charges more than this

# Edit stagnant detection
nano ~/autofee/autofee_stagnant_wrapper.py

# Key settings:
# STAGNANT_HOURS = 24  # Hours without routing to be considered stagnant
```

### Advanced Features

Once comfortable with basic operation, explore:

- **Custom pivot points** - Set specific liquidity targets per channel
- **Channel groups** - Synchronize fees across multiple channels
- **Minimum fees** - Enforce floor fees for specific channels

See the main README for detailed configuration options.

## Troubleshooting

### "No lncli command found"
```bash
# Add to PATH or use full path
which lncli
```

### "Permission denied on macaroon"
```bash
chmod 600 ~/autofee/charge-lnd.macaroon
```

### "No channels in INI file"
Check that `autofee_wrapper.py` ran successfully:
```bash
cat ~/autofee/avg_fees.json
tail ~/autofee/autofee_wrapper.log
```

### "charge-lnd errors"
Run with verbose flag to see details:
```bash
cd ~/autofee/charge-lnd && source venv/bin/activate
charge-lnd --macaroon ~/autofee/charge-lnd.macaroon \
  -c ~/autofee/dynamic_charge.ini --dry-run -v
```

### Fees changing too fast
Reduce `ADJUSTMENT_FACTOR` in `autofee_wrapper.py` to 0.02 or 0.03.

### Fees not changing enough
Increase `ADJUSTMENT_FACTOR` to 0.08 or 0.10.

## Complete!

Your node is now managing fees automatically based on actual routing data. Monitor for the first few days to ensure behavior matches expectations.

For detailed configuration options and advanced features, see the [main README](README.md).

---

**Questions or issues?** Check logs first:
```bash
# Main execution log
tail -100 ~/autofee/cron.log

# Component logs
tail -50 ~/autofee/autofee_wrapper.log
tail -50 ~/autofee/autofee_neginb_wrapper.log
tail -50 ~/autofee/autofee_stagnant_wrapper.log
```