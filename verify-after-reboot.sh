#!/bin/bash
set -u
k=$(uname -r)
echo '=== module ==='
modinfo -n nouveau
echo '=== DKMS ==='
dkms status -m nouveau-hpd-ddc -v 0.1.6 || true
echo '=== EDID ==='
for e in /sys/class/drm/card*-DVI-I-*/edid; do
    [ -e "$e" ] || continue
    printf '%s: ' "$e"
    stat -c '%s bytes' "$e"
done
echo '=== modes ==='
xrandr --query || true
echo '=== relevant boot log ==='
dmesg | grep -Ei 'DDC_DIAG|nouveau|edid|ddc|load.detect' | tail -n 100 || true
