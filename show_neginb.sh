#!/bin/bash

echo -e "Alias\t\t\t\tInbound Fee\tCurrent %\tLiq Ratio\tWas Above Threshold\tLast Action\t\t\t\t\t\tLast Updated"
echo -e "$(printf '%.0s-' {1..160})"

cat ~/autofee/neginb_fees.json | jq -r 'to_entries[] | "\(.key) \(.value.inbound_fee) \(.value.current_pct) \(.value.working_range_pct) \(.value.has_been_above_threshold) \(.value.last_updated)"' | while read scid inbound_fee current_pct working_range_pct has_been_above last_updated; do 
  alias=$(lncli getchaninfo "$scid" 2>/dev/null | jq -r --arg pubkey "$(lncli getinfo | jq -r .identity_pubkey)" 'if .node1_pub == $pubkey then .node2_pub else .node1_pub end' 2>/dev/null | xargs -I {} lncli getnodeinfo {} 2>/dev/null | jq -r '.node.alias // "Unknown"' 2>/dev/null | sed 's/[^a-zA-Z0-9._-]//g') || continue
  
  if [ -n "$alias" ]; then
    # Convert working_range_pct to ratio (divide by 100) and format to 2 decimal places
    liq_ratio=$(echo "scale=2; $working_range_pct / 100" | bc -l 2>/dev/null || echo "0.00")
    
    # Get the last action for this channel from the log
    last_action=$(tail -n 200 ~/autofee/autofee_neginb_wrapper.log | \
                  grep -E "(Initializing negative inbound|Incrementing negative inbound|Removing negative inbound|Adjusting negative inbound|Maintaining negative inbound|Below threshold.*never been above)" | \
                  grep "Channel $scid:" | \
                  tail -n 1 | \
                  sed 's/^[0-9-]* [0-9:,]* INFO: //' | \
                  sed "s/Channel $scid: //" | \
                  cut -c1-50)
    
    # If no action found, show "No recent action"
    if [ -z "$last_action" ]; then
      last_action="No recent action"
    fi
    
    printf "%-25s\t%s\t\t%s%%\t\t%.2f\t\t%s\t\t%-50s\t%s\n" \
           "${alias:0:25}" \
           "$inbound_fee" \
           "$current_pct" \
           "$liq_ratio" \
           "$has_been_above" \
           "$last_action" \
           "$(echo "$last_updated" | sed 's/T/ /' | cut -d'.' -f1 | cut -d'+' -f1)"
  fi
done