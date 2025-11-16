#!/bin/bash

# Check if both arguments are provided
if [ $# -ne 2 ]; then
    echo "Error: Both new fee and SCID must be specified"
    echo "Usage: $0 <new_avg_fee_ppm> <scid>"
    echo "Example: $0 100 996507179527241729"
    exit 1
fi

NEW_FEE=$1
SCID=$2

# Validate that NEW_FEE is a number
if ! [[ "$NEW_FEE" =~ ^[0-9]+$ ]]; then
    echo "Error: Please provide a valid number for the new average fee"
    echo "Usage: $0 <new_avg_fee_ppm> <scid>"
    exit 1
fi

# Validate that SCID is a number
if ! [[ "$SCID" =~ ^[0-9]+$ ]]; then
    echo "Error: Please provide a valid SCID (numeric)"
    echo "Usage: $0 <new_avg_fee_ppm> <scid>"
    exit 1
fi

# File paths
AVG_FEE_FILE="$HOME/autofee/avg_fees.json"
FEE_DB_FILE="$HOME/autofee/fee_history.db"

# Check if files exist
if [ ! -f "$AVG_FEE_FILE" ]; then
    echo "Error: avg_fees.json not found at $AVG_FEE_FILE"
    exit 1
fi

if [ ! -f "$FEE_DB_FILE" ]; then
    echo "Error: fee_history.db not found at $FEE_DB_FILE"
    exit 1
fi

# Get channel alias for confirmation
echo "Looking up channel information..."
ALIAS=$(lncli getchaninfo "$SCID" 2>/dev/null | jq -r --arg pubkey "$(lncli getinfo | jq -r .identity_pubkey)" 'if .node1_pub == $pubkey then .node2_pub else .node1_pub end' 2>/dev/null | xargs -I {} lncli getnodeinfo {} 2>/dev/null | jq -r '.node.alias // "Unknown"' 2>/dev/null | sed 's/[^a-zA-Z0-9._-]//g')

if [ -z "$ALIAS" ] || [ "$ALIAS" = "Unknown" ]; then
    echo "Warning: Could not retrieve channel alias (channel may not exist or be inactive)"
    ALIAS="Unknown"
fi

# Get current average fee
CURRENT_AVG=$(cat "$AVG_FEE_FILE" | jq -r --arg scid "$SCID" '.[$scid] // "Not found"')

# Get routing history count
ROUTING_COUNT=$(sqlite3 "$FEE_DB_FILE" "SELECT COUNT(*) FROM fee_history WHERE chan_id='$SCID';" 2>/dev/null || echo "0")

# Display current state and warning
echo
echo "==============================================="
echo "CHANNEL RESET CONFIRMATION"
echo "==============================================="
echo "SCID:           $SCID"
echo "Alias:          $ALIAS"
echo "Current Avg:    $CURRENT_AVG ppm"
echo "New Avg:        $NEW_FEE ppm"
echo "Routing Records: $ROUTING_COUNT forwards"
echo
echo "⚠️  WARNING: This will:"
echo "   • Delete ALL routing history for this channel"
echo "   • Reset the average fee to $NEW_FEE ppm"
echo "   • This action CANNOT be undone"
echo
echo "The channel will start fresh with no EMA history."
echo "==============================================="
echo

# Confirmation prompt
read -p "Are you sure you want to proceed? (type 'yes' to confirm): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Operation cancelled."
    exit 0
fi

echo
echo "Proceeding with reset..."

# Create backup of avg_fees.json
BACKUP_FILE="$AVG_FEE_FILE.backup.$(date +%Y%m%d_%H%M%S)"
cp "$AVG_FEE_FILE" "$BACKUP_FILE"
echo "✓ Created backup: $BACKUP_FILE"

# Delete routing history from database
DELETED_COUNT=$(sqlite3 "$FEE_DB_FILE" "DELETE FROM fee_history WHERE chan_id='$SCID'; SELECT changes();" 2>/dev/null || echo "0")
echo "✓ Deleted $DELETED_COUNT routing records from database"

# Update avg_fees.json
cat "$AVG_FEE_FILE" | jq --arg scid "$SCID" --arg fee "$NEW_FEE" '.[$scid] = ($fee | tonumber)' > "$AVG_FEE_FILE.tmp"

if [ $? -eq 0 ]; then
    mv "$AVG_FEE_FILE.tmp" "$AVG_FEE_FILE"
    echo "✓ Updated average fee to $NEW_FEE ppm in avg_fees.json"
else
    echo "✗ Error updating avg_fees.json"
    rm -f "$AVG_FEE_FILE.tmp"
    exit 1
fi

echo
echo "==============================================="
echo "RESET COMPLETE"
echo "==============================================="
echo "Channel $SCID ($ALIAS) has been reset:"
echo "• Average fee: $NEW_FEE ppm"
echo "• Routing history: Cleared"
echo "• Backup saved: $BACKUP_FILE"
echo
echo "The channel will rebuild its EMA from new routing activity."
echo "==============================================="