#!/bin/bash

# Configuration
JSON_FILE="devices.json"
LOG_FILE="network_monitor.log"

# Check if JSON file exists
if [ ! -f "$JSON_FILE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $JSON_FILE not found!" | tee -a "$LOG_FILE"
    exit 1
fi

echo "========================================="
echo "Starting Azure Network Monitoring Scan..."
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

# Read JSON and parse items using grep/sed
grep -o '"ip": "[^"]*"' "$JSON_FILE" | sed 's/"ip": "//;s/"//' | while read -r ip; do
    
    # Get the name of the device for better logging
    name=$(grep -B 1 "$ip" "$JSON_FILE" | grep '"name"' | sed 's/"name": "//;s/",//;s/^[ \t]*//')

    # Ping the device (1 packet, 2 seconds timeout)
    if ping -c 1 -w 2 "$ip" > /dev/null 2>&1; then
        echo -e "\e[32m[ONLINE]\e[0m $name ($ip) is reachable."
    else
        # Log the failure with precise timestamp
        TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
        LOG_MESSAGE="[$TIMESTAMP] ALERT: Device Down -> Name: $name | IP: $ip"
        
        # Print to screen in red AND append to log file
        echo -e "\e[31m[OFFLINE]\e[0m $name ($ip) is NOT reachable!"
        echo "$LOG_MESSAGE" >> "$LOG_FILE"
    fi
done

echo "-----------------------------------------"
echo "Scan complete. Logs saved to $LOG_FILE"
echo "========================================="
