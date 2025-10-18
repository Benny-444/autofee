#!/bin/bash
echo -e "SCID\t\t\tAlias\t\t\t\tRatio\tAvg Fee\tTarget\tCurrent\tNew\tLast Update"
echo -e "$(printf '%.0s-' {1..140})"

# Get the last 100 lines from the log to find recent channel updates
tail -n 100 ~/autofee/autofee_wrapper.log | \
grep "Channel.*avg_fee=" | \
tail -n 50 | \
while read line; do
    # Extract channel ID (format: "Channel 570e18b421a0a9f9d524eb81630accc6ac8eed24ebd05a19efdbc4bcf8390d3c:")
    chan_id=$(echo "$line" | grep -oE "Channel [0-9a-f]{64}:" | grep -oE "[0-9a-f]{64}")
    
    if [ -n "$chan_id" ]; then
        # Get SCID from channel ID
        scid=$(lncli listchannels 2>/dev/null | jq -r --arg cid "$chan_id" '.channels[] | select(.chan_id == $cid) | .scid')
        
        if [ -n "$scid" ] && [ "$scid" != "null" ]; then
            # Get channel alias
            alias=$(lncli listchannels 2>/dev/null | jq -r --arg cid "$chan_id" '.channels[] | select(.chan_id == $cid) | .remote_pubkey' | \
                   xargs -I {} lncli getnodeinfo {} 2>/dev/null | \
                   jq -r '.node.alias // "Unknown"' | \
                   sed 's/[^a-zA-Z0-9._-]//g')
            
            # Extract values from log line
            # Format: "Channel ID: avg_fee=X, ratio=Y, current=Z, target=W, new=V"
            avg_fee=$(echo "$line" | grep -oE "avg_fee=[0-9]+" | cut -d'=' -f2)
            ratio=$(echo "$line" | grep -oE "ratio=[0-9.]+" | cut -d'=' -f2)
            current=$(echo "$line" | grep -oE "current=[0-9]+" | cut -d'=' -f2)
            target=$(echo "$line" | grep -oE "target=[0-9]+" | cut -d'=' -f2)
            new=$(echo "$line" | grep -oE "new=[0-9.]+" | cut -d'=' -f2)
            
            # Extract timestamp
            timestamp=$(echo "$line" | grep -oE "^[0-9-]+ [0-9:]+" | cut -d',' -f1)
            
            if [ -n "$avg_fee" ] && [ -n "$ratio" ]; then
                printf "%-15s\t%-25s\t%.2f\t%s\t%s\t%s\t%s\t%s\n" \
                       "$scid" \
                       "${alias:0:25}" \
                       "$ratio" \
                       "$avg_fee" \
                       "$target" \
                       "$current" \
                       "$new" \
                       "$timestamp"
            fi
        fi
    fi
done | \
# Remove duplicates based on first column (SCID) and keep last occurrence
awk '!seen[$1]++ {order[++i] = $0; scid[i] = $1} 
     seen[$1] > 1 {
         for(j=1; j<=i; j++) {
             if(scid[j] == $1) {
                 order[j] = $0
                 break
             }
         }
     }
     END {for(j=1; j<=i; j++) print order[j]}'