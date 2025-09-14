#!/bin/bash

# Check if both arguments are provided
if [ $# -ne 2 ]; then
    echo "Error: Both new inbound fee and SCID must be specified"
    echo "Usage: $0 <new_inbound_fee_ppm> <scid>"
    echo "Example: $0 -150 996507179527241729"
    echo "Example: $0 0 996507179527241729"
    exit 1
fi

NEW_FEE=$1
SCID=$2

# Validate that NEW_FEE is a number (including negative)
if ! [[ "$NEW_FEE" =~ ^-?[0-9]+$ ]]; then
    echo "Error: Please provide a valid number for the new inbound fee"
    echo "Usage: $0 <new_inbound_fee_ppm> <scid>"
    exit 1
fi

# Validate that SCID is a number
if ! [[ "$SCID" =~ ^[0-9]+$ ]]; then
    echo "Error: Please provide a valid SCID (numeric)"
    echo "Usage: $0 <new_inbound_fee_ppm> <scid>"
    exit 1
fi

# File path
NEGINB_FILE="$HOME/autofee/neginb_fees.json"

# Check if file exists
if [ ! -f "$NEGINB_FILE" ]; then
    echo "Error: neginb_fees.json not found at $NEGINB_FILE"
    exit 1
fi

# Get channel alias for confirmation
echo "Looking up channel information..."
ALIAS=$(lncli getchaninfo "$SCID" 2>/dev/null | jq -r --arg pubkey "$(lncli getinfo | jq -r .identity_pubkey)" 'if .node1_pub == $pubkey then .node2_pub else .node1_pub end' 2>/dev/null | xargs -I {} lncli getnodeinfo {} 2>/dev/null | jq -r '.node.alias // "Unknown"' 2>/dev/null | sed 's/[^a-zA-Z0-9._-]//g')

if [ -z "$ALIAS" ] || [ "$ALIAS" = "Unknown" ]; then
    echo "Warning: Could not retrieve channel alias (channel may not exist or be inactive)"
    ALIAS="Unknown"
fi

# Get current inbound fee (if channel exists in file)
CURRENT_FEE=$(cat "$NEGINB_FILE" | jq -r --arg scid "$SCID" '.[$scid].inbound_fee // "Not found"')

# Display current state and warning
echo
echo "==============================================="
echo "NEGATIVE INBOUND FEE RESET CONFIRMATION"
echo "==============================================="
echo "SCID:            $SCID"
echo "Alias:           $ALIAS"
echo "Current Inbound: $CURRENT_FEE ppm"
echo "New Inbound:     $NEW_FEE ppm"
echo
echo "⚠️  WARNING: This will:"
echo "   • Set the inbound fee to $NEW_FEE ppm"
echo "   • Reset current_pct to match the new fee"
echo "   • This action CANNOT be undone"
echo
echo "The channel's inbound fee state will be updated."
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

# Create backup of neginb_fees.json
BACKUP_FILE="$NEGINB_FILE.backup.$(date +%Y%m%d_%H%M%S)"
cp "$NEGINB_FILE" "$BACKUP_FILE"
echo "✓ Created backup: $BACKUP_FILE"

# Calculate current_pct based on new fee (assuming we know avg_fee)
# For simplicity, if setting to 0, set current_pct to 0
# If setting to negative value, we'll need to calculate percentage
if [ "$NEW_FEE" -eq 0 ]; then
    NEW_PCT=0
else
    # Try to get avg_fee for percentage calculation, default to reasonable estimate
    AVG_FEE=$(cat "$HOME/autofee/avg_fees.json" 2>/dev/null | jq -r --arg scid "$SCID" '.[$scid] // 100')
    if [ "$AVG_FEE" != "null" ] && [ "$AVG_FEE" -gt 0 ]; then
        NEW_PCT=$(echo "scale=0; ($NEW_FEE * -100) / $AVG_FEE" | bc 2>/dev/null || echo "0")
    else
        NEW_PCT=30  # Default reasonable percentage
    fi
fi

# Update neginb_fees.json
cat "$NEGINB_FILE" | jq --arg scid "$SCID" --arg fee "$NEW_FEE" --arg pct "$NEW_PCT" --arg timestamp "$(date -Iseconds)" '
  .[$scid].inbound_fee = ($fee | tonumber) |
  .[$scid].current_pct = ($pct | tonumber) |
  .[$scid].last_updated = $timestamp
' > "$NEGINB_FILE.tmp"

if [ $? -eq 0 ]; then
    mv "$NEGINB_FILE.tmp" "$NEGINB_FILE"
    echo "✓ Updated inbound fee to $NEW_FEE ppm in neginb_fees.json"
else
    echo "✗ Error updating neginb_fees.json"
    rm -f "$NEGINB_FILE.tmp"
    exit 1
fi

echo
echo "==============================================="
echo "RESET COMPLETE"
echo "==============================================="
echo "Channel $SCID ($ALIAS) has been reset:"
echo "• Inbound fee: $NEW_FEE ppm"
echo "• Current percentage: $NEW_PCT%"
echo "• Backup saved: $BACKUP_FILE"
echo
echo "The channel's inbound fee has been updated."
echo "==============================================="