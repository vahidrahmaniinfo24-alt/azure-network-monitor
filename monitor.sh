#!/bin/bash
echo "⏳ Überprüfe den Status der Azure-Netzwerkgeräte..."
echo "--------------------------------------------------"

grep -B 3 '"status": "offline"' devices.json | grep '"name":' | awk -F'"' '{print "❌ Offline: " $4}'

echo "--------------------------------------------------"
echo "✅ Überprüfung abgeschlossen!"
