#!/bin/bash

# Read settings from the Python script
STAGNANT_HOURS=$(grep "^STAGNANT_HOURS = " ~/autofee/autofee_stagnant_wrapper.py | sed 's/STAGNANT_HOURS = //' | sed 's/ *#.*//')
STAGNANT_RATIO_THRESHOLD=$(grep "^STAGNANT_RATIO_THRESHOLD = " ~/autofee/autofee_stagnant_wrapper.py | sed 's/STAGNANT_RATIO_THRESHOLD = //' | sed 's/ *#.*//')

echo -e "SCID\t\t\tAlias\t\t\t\tStagnant\tLast Ratio\tLast Forward\t\tTime to Stagnant" && echo -e "$(printf '%.0s-' {1..140})" && cat ~/autofee/stagnant_state.json | jq -r 'to_entries[] | "\(.key) \(.value.is_stagnant) \(.value.last_ratio) \(.value.last_change)"' | while read scid is_stagnant last_ratio last_change; do 
  alias=$(lncli getchaninfo "$scid" 2>/dev/null | jq -r --arg pubkey "$(lncli getinfo | jq -r .identity_pubkey)" 'if .node1_pub == $pubkey then .node2_pub else .node1_pub end' 2>/dev/null | xargs -I {} lncli getnodeinfo {} 2>/dev/null | jq -r '.node.alias // "Unknown"' 2>/dev/null | sed 's/[^a-zA-Z0-9._-]//g') || continue
  
  if [ -n "$alias" ]; then
    # Get last forward time from database
    last_forward_timestamp=$(sqlite3 ~/autofee/fee_history.db "SELECT MAX(timestamp) FROM fee_history WHERE chan_id='$scid';" 2>/dev/null || echo "")
    
    current_time=$(date +%s)
    current_ratio_pct=$(echo "$last_ratio * 100" | bc -l)
    threshold_pct=$(echo "$STAGNANT_RATIO_THRESHOLD * 100" | bc -l)
    
    if [ "$is_stagnant" = "true" ]; then
      time_to_stagnant="Already Stagnant"
      if [ -n "$last_forward_timestamp" ] && [ "$last_forward_timestamp" != "" ]; then
        last_forward_display=$(date -d "@$last_forward_timestamp" "+%Y-%m-%d %H:%M" 2>/dev/null || echo "Unknown")
      else
        last_forward_display="No forwards        "  # Added 8 spaces
      fi
    elif (( $(echo "$current_ratio_pct < $threshold_pct" | bc -l) )); then
      time_to_stagnant="Below threshold"
      if [ -n "$last_forward_timestamp" ] && [ "$last_forward_timestamp" != "" ]; then
        last_forward_display=$(date -d "@$last_forward_timestamp" "+%Y-%m-%d %H:%M" 2>/dev/null || echo "Unknown")
      else
        last_forward_display="No forwards        "  # Added 8 spaces
      fi
    else
      # Channel is above threshold - calculate time based on last forward
      if [ -n "$last_forward_timestamp" ] && [ "$last_forward_timestamp" != "" ]; then
        # Has forwards - calculate time since last forward
        hours_since_forward=$(( (current_time - last_forward_timestamp) / 3600 ))
        remaining_hours=$(( STAGNANT_HOURS - hours_since_forward ))
        last_forward_display=$(date -d "@$last_forward_timestamp" "+%Y-%m-%d %H:%M" 2>/dev/null || echo "Unknown")
        
        if [ $remaining_hours -le 0 ]; then
          time_to_stagnant="Should be stagnant"
        else
          remaining_minutes=$(( ((STAGNANT_HOURS * 3600) - (current_time - last_forward_timestamp)) / 60 % 60 ))
          time_to_stagnant="${remaining_hours}h ${remaining_minutes}m"
        fi
      else
        # No forwards in database - use last_change as fallback
        last_change_time=$(date -d "$last_change" +%s 2>/dev/null || echo "$current_time")
        elapsed_hours=$(( (current_time - last_change_time) / 3600 ))
        remaining_hours=$(( STAGNANT_HOURS - elapsed_hours ))
        last_forward_display="No forwards        "  # Added 8 spaces
        
        if [ $remaining_hours -le 0 ]; then
          time_to_stagnant="Should be stagnant"
        else
          remaining_minutes=$(( (STAGNANT_HOURS * 3600 - (current_time - last_change_time)) / 60 % 60 ))
          time_to_stagnant="${remaining_hours}h ${remaining_minutes}m"
        fi
      fi
    fi
    
    printf "%-15s\t%-25s\t%s\t\t%.2f%%\t\t%s\t%s\n" "$scid" "${alias:0:25}" "$is_stagnant" "$(echo "$last_ratio * 100" | bc -l)" "$last_forward_display" "$time_to_stagnant"
  fi
done