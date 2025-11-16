#!/bin/bash

echo -e "Alias\t\t\t\tLiq Ratio\tLast Action"
echo -e "$(printf '%.0s-' {1..120})"

# Get the last 100 lines from stagnant log and filter for outbound fee reductions only
tail -n 100 ~/autofee/autofee_stagnant_wrapper.log | \
grep -E "Channel [a-f0-9]{64}:.*Reduced outbound fee" | \
tail -n 50 | \
while read line; do
    # Extract channel ID (64 hex characters after "Channel ")
    chan_id=$(echo "$line" | grep -oE "Channel [a-f0-9]{64}:" | grep -oE "[a-f0-9]{64}")
    
    if [ -n "$chan_id" ]; then
        # Get SCID from channel ID
        scid=$(lncli listchannels 2>/dev/null | jq -r --arg cid "$chan_id" '.channels[] | select(.chan_id == $cid) | .scid')
        
        if [ -n "$scid" ] && [ "$scid" != "null" ]; then
            # Get channel alias
            alias=$(lncli listchannels 2>/dev/null | jq -r --arg cid "$chan_id" '.channels[] | select(.chan_id == $cid) | .remote_pubkey' | \
                   xargs -I {} lncli getnodeinfo {} 2>/dev/null | \
                   jq -r '.node.alias // "Unknown"' | \
                   sed 's/[^a-zA-Z0-9._-]//g')
            
            # Get channel liquidity info
            chan_info=$(lncli listchannels 2>/dev/null | jq -r --arg cid "$chan_id" '.channels[] | select(.chan_id == $cid) | "\(.local_balance) \(.capacity)"')
            local_balance=$(echo "$chan_info" | cut -d' ' -f1)
            capacity=$(echo "$chan_info" | cut -d' ' -f2)
            
            if [ -n "$local_balance" ] && [ -n "$capacity" ] && [ "$capacity" -gt 0 ]; then
                ratio=$(echo "scale=1; $local_balance * 100 / $capacity" | bc 2>/dev/null || echo "0.0")
                
                # Clean up the message - remove timestamp and replace chan_id with scid
                message=$(echo "$line" | \
                         sed 's/^[0-9-]* [0-9:,]* INFO: //' | \
                         sed "s/Channel $chan_id:/Channel $scid:/")
                
                printf "%-25s\t%s%%\t\t%s\n" "${alias:0:25}" "$ratio" "$message"
            fi
        fi
    fi
done | \
# Remove duplicates based on the first column (alias) and keep the last occurrence
awk '!seen[$1]++ {order[++i] = $0; alias[i] = $1} 
     seen[$1] > 1 {
         for(j=1; j<=i; j++) {
             if(alias[j] == $1) {
                 order[j] = $0
                 break
             }
         }
     }
     END {for(j=1; j<=i; j++) print order[j]}'