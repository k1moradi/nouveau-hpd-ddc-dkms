#!/bin/bash
set -u

NAME=nouveau-hpd-ddc
VER=0.1.6
k=$(uname -r)

read_boot_log() {
    if [ -n "${NOUVEAU_VERIFY_LOG:-}" ]; then
        cat -- "$NOUVEAU_VERIFY_LOG"
        return
    fi

    if command -v journalctl >/dev/null 2>&1; then
        journal=$(journalctl -k -b --no-pager 2>/dev/null || true)
        if [ -n "$journal" ]; then
            printf '%s\n' "$journal"
            return
        fi
    fi

    dmesg 2>/dev/null || true
}

powered_ddc_summary() {
    stage=$1
    lines=$(printf '%s\n' "$boot_log" |
        grep -F "DDC_DIAG: DAC_POWERED_DDC stage=$stage " || true)

    if [ -z "$lines" ]; then
        printf 'NO SAMPLE'
        return
    fi

    success=$(printf '%s\n' "$lines" | grep -E ' ret=2 edid0=[[:xdigit:]]{2}([[:space:]]|$)' | tail -n 1 || true)
    if [ -n "$success" ]; then
        ret=$(printf '%s\n' "$success" | sed -n 's/.* ret=\(-\{0,1\}[0-9][0-9]*\) .*/\1/p')
        edid0=$(printf '%s\n' "$success" | sed -n 's/.* edid0=\([[:xdigit:]][[:xdigit:]]\).*/\1/p')
        if [ "$edid0" = 00 ]; then
            printf 'ACK ret=%s edid0=%s' "$ret" "$edid0"
        else
            printf 'ACK ret=%s edid0=%s (unexpected EDID byte 0)' "$ret" "$edid0"
        fi
        return
    fi

    partial=$(printf '%s\n' "$lines" | grep -E ' ret=1 edid0=[[:xdigit:]]{2}([[:space:]]|$)' | tail -n 1 || true)
    if [ -n "$partial" ]; then
        edid0=$(printf '%s\n' "$partial" | sed -n 's/.* edid0=\([[:xdigit:]][[:xdigit:]]\).*/\1/p')
        printf 'PARTIAL ret=1%s' "${edid0:+ edid0=$edid0}"
        return
    fi

    latest=$(printf '%s\n' "$lines" | tail -n 1)
    ret=$(printf '%s\n' "$latest" | sed -n 's/.* ret=\(-\{0,1\}[0-9][0-9]*\) .*/\1/p')
    edid0=$(printf '%s\n' "$latest" | sed -n 's/.* edid0=\([[:xdigit:]][[:xdigit:]]\).*/\1/p')
    if [ -z "$ret" ]; then
        printf 'UNPARSED: %s' "$latest"
    elif [ "$ret" = 1 ]; then
        printf 'PARTIAL ret=1%s' "${edid0:+ edid0=$edid0}"
    elif [ "$ret" = -6 ]; then
        printf 'FAILED ret=-6 (ENXIO)%s' "${edid0:+ edid0=$edid0}"
    else
        printf 'FAILED ret=%s%s' "$ret" "${edid0:+ edid0=$edid0}"
    fi
}

powered_ddc_acked() {
    stage=$1
    printf '%s\n' "$boot_log" |
        grep -F "DDC_DIAG: DAC_POWERED_DDC stage=$stage " |
        grep -Eq ' ret=2 edid0=[[:xdigit:]]{2}([[:space:]]|$)'
}

powered_ddc_partial() {
    stage=$1
    printf '%s\n' "$boot_log" |
        grep -F "DDC_DIAG: DAC_POWERED_DDC stage=$stage " |
        grep -Eq ' ret=1 edid0=[[:xdigit:]]{2}([[:space:]]|$)'
}

preload_ddc_summary() {
    bus_hex=$(printf '%s\n' "$boot_log" |
        sed -n 's/.*DDC_DIAG: DAC_POWERED_DDC .* bus=\([[:xdigit:]]\{4\}\) .*/\1/p' |
        tail -n 1)

    if [ -z "$bus_hex" ]; then
        printf 'NO TRACE SAMPLE'
        return
    fi

    bus_num=$((16#$bus_hex))
    line=$(printf '%s\n' "$boot_log" |
        grep -E "i2c_result: i2c-${bus_num} .*ret=-?[0-9]+" |
        head -n 1 || true)
    if [ -z "$line" ]; then
        printf 'NO TRACE SAMPLE'
        return
    fi

    ret=$(printf '%s\n' "$line" | sed -n 's/.*ret=\(-\{0,1\}[0-9][0-9]*\).*/\1/p')
    if [ "$ret" = 2 ]; then
        printf 'COMPLETE ret=2'
    elif [ "$ret" = 1 ]; then
        printf 'PARTIAL ret=1'
    elif [ "$ret" = -6 ]; then
        printf 'FAILED ret=-6 (ENXIO)'
    else
        printf 'FAILED ret=%s' "$ret"
    fi
}

boot_log=$(read_boot_log)

module_path=$(modinfo -n nouveau 2>/dev/null || true)
module_vermagic=$(modinfo -F vermagic nouveau 2>/dev/null || true)
module_srcversion=$(modinfo -F srcversion nouveau 2>/dev/null || true)
loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
dkms_status=$(dkms status -m "$NAME" -v "$VER" 2>/dev/null || true)

echo '=== module ==='
printf 'kernel: %s\n' "$k"
printf 'nouveau: %s\n' "${module_path:-NOT FOUND}"
printf 'vermagic: %s\n' "${module_vermagic:-UNKNOWN}"
printf 'resolved srcversion: %s\n' "${module_srcversion:-UNKNOWN}"
printf 'loaded srcversion:   %s\n' "${loaded_srcversion:-UNKNOWN}"
if [ ! -d /sys/module/nouveau ]; then
    echo 'running DKMS module: NO (nouveau is not loaded)'
else
    case "$module_path" in
        */updates/dkms/*|*/extra/*)
            if [ -n "$module_srcversion" ] && [ -n "$loaded_srcversion" ]; then
                if [ "$module_srcversion" = "$loaded_srcversion" ]; then
                    echo 'running DKMS module: YES (DKMS path and srcversion match)'
                else
                    echo 'running DKMS module: NO (loaded srcversion differs from DKMS module file)'
                fi
            else
                echo 'running DKMS module: LIKELY (DKMS path selected; srcversion unavailable)'
            fi
            ;;
        "")
            echo 'running DKMS module: UNKNOWN (modinfo failed)'
            ;;
        *)
            echo 'running DKMS module: NO/UNCONFIRMED (resolved path is not a DKMS override path)'
            ;;
    esac
fi

echo '=== DKMS ==='
if [ -n "$dkms_status" ]; then
    printf '%s\n' "$dkms_status"
else
    echo "$NAME/$VER: no DKMS status returned"
fi

echo '=== EDID ==='
found_edid=0
for e in /sys/class/drm/card*-DVI-I-*/edid; do
    [ -e "$e" ] || continue
    found_edid=1
    printf '%s: ' "$e"
    stat -c '%s bytes' "$e"
done
if [ "$found_edid" -eq 0 ]; then
    echo 'no DVI-I EDID sysfs file found'
fi

echo '=== modes ==='
xrandr --query 2>/dev/null || true

echo '=== DDC diagnostic summary ==='
printf 'PRE_LOAD_DDC:          %s\n' "$(preload_ddc_summary)"
printf 'DAC_POWERED_IMMEDIATE: %s\n' "$(powered_ddc_summary power-on)"
printf 'DAC_POWERED_SETTLED:   %s\n' "$(powered_ddc_summary load-sense)"

echo '=== interpretation ==='
power_on_ack=0
load_sense_ack=0
partial_response=0
if powered_ddc_acked power-on; then
    power_on_ack=1
fi
if powered_ddc_acked load-sense; then
    load_sense_ack=1
fi
if powered_ddc_partial power-on || powered_ddc_partial load-sense; then
    partial_response=1
fi

if [ "$power_on_ack" -eq 1 ] && [ "$load_sense_ack" -eq 1 ]; then
    echo 'DDC ACK is present immediately after DAC power-up and remains present during load sense.'
    echo 'DAC-powered state is strongly implicated; next inspect the minimal prerequisite needed before normal EDID probing.'
elif [ "$power_on_ack" -eq 0 ] && [ "$load_sense_ack" -eq 1 ]; then
    echo 'DDC does not ACK immediately after DAC power-up but does ACK after the load-sense settling interval.'
    echo 'Load-sense/settling state is implicated more strongly than DAC acquisition alone.'
elif [ "$power_on_ack" -eq 1 ] && [ "$load_sense_ack" -eq 0 ]; then
    echo 'DDC ACK was observed immediately after DAC power-up but not during the later load-sense probe.'
    echo 'The powered state changes DDC behavior, but the result is inconsistent/transient; repeat before inferring a prerequisite.'
elif [ "$partial_response" -eq 1 ]; then
    echo 'At least one DAC-powered probe completed one of the two I2C messages (ret=1).'
    echo 'That differs from a total ENXIO failure and keeps the DAC-power hypothesis viable; repeat with tracing before drawing a root-cause conclusion.'
elif printf '%s\n' "$boot_log" | grep -qF 'DDC_DIAG: DAC_POWERED_DDC '; then
    echo 'Neither load-detect-state probe received a complete two-message DDC response.'
    echo 'The DAC/load-sense hypothesis is weakened; known pad_x, GPIO31, and e1b8 theories are already statically weak.'
else
    echo 'No DAC-powered DDC diagnostic samples were found in this boot log.'
    echo 'Result is inconclusive: verify that the --diag-dac-ddc DKMS build is installed and that analog load detection ran.'
fi

echo '=== relevant boot log ==='
printf '%s\n' "$boot_log" |
    grep -Ei 'DDC_DIAG|i2c_result|nouveau|edid|ddc|load.detect' |
    tail -n 150 || true
