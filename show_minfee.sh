#!/bin/bash
echo -e "SCID\t\t\tAlias\t\t\t\tMin Type\tMin Value\tCurrent\tNew Fee\tAction\t\tLast Update"
echo -e "$(printf '%.0s-' {1..150})"

# Get the last 200 lines from the log to find recent minimum fee operations
tail -n 200 ~/autofee/autofee_minfee_wrapper.log | \
grep "Channel.*:" | \
grep -E "(Calculated minimum|already >=|Raised fee)" | \
while read line; do
    # Extract channel SCID
    scid=$(echo "$line" | grep -oE "Channel [0-9]+:" | grep -oE "[0-9]+")
    
    if [ -n "$scid" ]; then
        # Get channel alias
        alias=$(lncli getchaninfo "$scid" 2>/dev/null | \
               jq -r --arg pubkey "$(lncli getinfo | jq -r .identity_pubkey)" \
               'if .node1_pub == $pubkey then .node2_pub else .node1_pub end' 2>/dev/null | \
               xargs -I {} lncli getnodeinfo {} 2>/dev/null | \
               jq -r '.node.alias // "Unknown"' 2>/dev/null | \
               sed 's/[^a-zA-Z0-9._-]//g')
        
        # Extract timestamp
        timestamp=$(echo "$line" | grep -oE "^[0-9-]+ [0-9:]+")
        
        # Determine if this is a "Calculated minimum" line, "already ok" line, or "Raised fee" line
        if echo "$line" | grep -q "Calculated minimum"; then
            # Extract minimum calculation details
            avg_fee=$(echo "$line" | grep -oE "avg_fee [0-9]+ ppm" | grep -oE "[0-9]+")
            percentage=$(echo "$line" | grep -oE "[0-9]+%" | grep -oE "[0-9]+")
            min_value=$(echo "$line" | grep -oE "= [0-9]+ ppm" | grep -oE "[0-9]+")
            
            if [ -n "$percentage" ]; then
                min_type="avg_fee ${percentage}%"
            else
                min_type="avg_fee 100%"
            fi
            
            # Store these for the next line (which will be the action line)
            current_scid="$scid"
            current_alias="$alias"
            current_min_type="$min_type"
            current_min_value="$min_value"
            current_timestamp="$timestamp"
            
        elif echo "$line" | grep -q "already >="; then
            # Channel already meets minimum
            if [ "$scid" = "$current_scid" ]; then
                current_fee=$(echo "$line" | grep -oE "Fee [0-9]+ ppm" | grep -oE "[0-9]+")
                
                printf "%-15s\t%-25s\t%-12s\t%s\t\t%s\t%s\t%-12s\t%s\n" \
                       "$scid" \
                       "${current_alias:0:25}" \
                       "$current_min_type" \
                       "$current_min_value" \
                       "$current_fee" \
                       "-" \
                       "OK" \
                       "$current_timestamp"
            fi
            
        elif echo "$line" | grep -q "Raised fee"; then
            # Channel fee was raised
            if [ "$scid" = "$current_scid" ]; then
                old_fee=$(echo "$line" | grep -oE "from [0-9]+" | grep -oE "[0-9]+")
                new_fee=$(echo "$line" | grep -oE "to [0-9]+ ppm" | grep -oE "[0-9]+")
                
                # Check if this is a static minimum or avg_fee minimum
                if echo "$line" | grep -q "minimum: static"; then
                    current_min_type="static"
                    static_min=$(echo "$line" | grep -oE "static \([0-9]+\)" | grep -oE "[0-9]+")
                    current_min_value="$static_min"
                fi
                
                printf "%-15s\t%-25s\t%-12s\t%s\t\t%s\t%s\t%-12s\t%s\n" \
                       "$scid" \
                       "${current_alias:0:25}" \
                       "$current_min_type" \
                       "$current_min_value" \
                       "$old_fee" \
                       "$new_fee" \
                       "Raised" \
                       "$current_timestamp"
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

# Show summary from last run
echo ""
echo "=== Last Run Summary ==="
tail -n 50 ~/autofee/autofee_minfee_wrapper.log | \
grep -E "(Starting minimum fee enforcement|Minimum fee enforcement complete)" | \
tail -n 2