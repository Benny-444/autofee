#!/bin/bash
echo "========== Autofee run started at $(date) ==========" >> /home/admin/autofee/cron.log

# Run outbound script to generate initial INI
if ! /home/admin/autofee/autofee_wrapper.py >> /home/admin/autofee/cron.log 2>&1; then
    echo "ERROR: Outbound script failed at $(date)" >> /home/admin/autofee/cron.log
    exit 1
fi

# Short pause for file system sync
sleep 2

# Run inbound script to update the INI with inbound fees
if ! /home/admin/autofee/autofee_neginb_wrapper.py >> /home/admin/autofee/cron.log 2>&1; then
    echo "ERROR: Inbound script failed at $(date)" >> /home/admin/autofee/cron.log
    # Continue anyway - we still have valid outbound fees
fi

# Short pause for file system sync
sleep 2

# Run stagnant script to update the INI with fee reductions to stagnant channels
if ! /home/admin/autofee/autofee_stagnant_wrapper.py >> /home/admin/autofee/cron.log 2>&1; then
    echo "ERROR: Stagnant script failed at $(date)" >> /home/admin/autofee/cron.log
    # Continue anyway - we still have valid outbound/inbound fees
fi

# Short pause for file system sync
sleep 2

# Run max HTLC script to update the INI with max HTLC values
if ! /home/admin/autofee/autofee_maxhtlc_wrapper.py >> /home/admin/autofee/cron.log 2>&1; then
    echo "ERROR: Max HTLC script failed at $(date)" >> /home/admin/autofee/cron.log
    # Continue anyway - we still have valid fees
fi

# Short pause for file system sync
sleep 2

# Apply all fees (outbound, inbound, stagnant reductions, and max HTLC) in one go
cd /home/admin/autofee/charge-lnd
source venv/bin/activate
if ! charge-lnd --macaroon /home/admin/autofee/charge-lnd.macaroon -c /home/admin/autofee/dynamic_charge.ini -v >> /home/admin/autofee/cron.log 2>&1; then
    echo "ERROR: charge-lnd failed at $(date)" >> /home/admin/autofee/cron.log
    deactivate
    exit 1
fi
deactivate

echo "========== Autofee run completed at $(date) ==========" >> /home/admin/autofee/cron.log
