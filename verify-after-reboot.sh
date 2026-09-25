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

powered_ddc_lines() {
    stage=$1
    printf '%s\n' "$boot_log" |
        grep -F "DDC_DIAG: DAC_POWERED_DDC stage=$stage " || true
}

powered_ddc_sample_count() {
    stage=$1
    powered_ddc_lines "$stage" | awk 'NF { count++ } END { print count + 0 }'
}

powered_ddc_complete_count() {
    stage=$1
    powered_ddc_lines "$stage" |
        grep -Ec ' ret=2 edid0=[[:xdigit:]]{2}([[:space:]]|$)' || true
}

powered_ddc_partial_count() {
    stage=$1
    powered_ddc_lines "$stage" |
        grep -Ec ' ret=1 edid0=[[:xdigit:]]{2}([[:space:]]|$)' || true
}

powered_ddc_summary() {
    stage=$1
    lines=$(powered_ddc_lines "$stage")

    if [ -z "$lines" ]; then
        printf 'NO SAMPLE'
        return
    fi

    samples=$(printf '%s\n' "$lines" | awk 'NF { count++ } END { print count + 0 }')
    complete=$(printf '%s\n' "$lines" |
        grep -Ec ' ret=2 edid0=[[:xdigit:]]{2}([[:space:]]|$)' || true)
    partial=$(printf '%s\n' "$lines" |
        grep -Ec ' ret=1 edid0=[[:xdigit:]]{2}([[:space:]]|$)' || true)
    latest=$(printf '%s\n' "$lines" | tail -n 1)
    ret=$(printf '%s\n' "$latest" | sed -n 's/.* ret=\(-\{0,1\}[0-9][0-9]*\) .*/\1/p')
    edid0=$(printf '%s\n' "$latest" | sed -n 's/.* edid0=\([[:xdigit:]][[:xdigit:]]\).*/\1/p')

    if [ "$complete" -gt 0 ] && [ "$complete" -lt "$samples" ]; then
        printf 'MIXED complete=%s/%s partial=%s latest_ret=%s%s' \
            "$complete" "$samples" "$partial" "${ret:-UNPARSED}" \
            "${edid0:+ edid0=$edid0}"
    elif [ "$complete" -eq "$samples" ]; then
        if [ "$edid0" = 00 ]; then
            printf 'ACK ret=2 edid0=%s samples=%s/%s' "$edid0" "$complete" "$samples"
        else
            printf 'ACK ret=2 edid0=%s samples=%s/%s (unexpected EDID byte 0)' \
                "${edid0:-??}" "$complete" "$samples"
        fi
    elif [ "$partial" -gt 0 ] && [ "$partial" -eq "$samples" ]; then
        printf 'PARTIAL ret=1 samples=%s/%s%s' "$partial" "$samples" \
            "${edid0:+ edid0=$edid0}"
    elif [ -z "$ret" ]; then
        printf 'UNPARSED: %s' "$latest"
    elif [ "$ret" = 1 ]; then
        printf 'PARTIAL ret=1%s' "${edid0:+ edid0=$edid0}"
    elif [ "$ret" = -6 ]; then
        printf 'FAILED ret=-6 (ENXIO)%s' "${edid0:+ edid0=$edid0}"
    else
        printf 'FAILED ret=%s%s' "$ret" "${edid0:+ edid0=$edid0}"
    fi
}

powered_ddc_all_acked() {
    stage=$1
    samples=$(powered_ddc_sample_count "$stage")
    complete=$(powered_ddc_complete_count "$stage")
    [ "$samples" -gt 0 ] && [ "$complete" -eq "$samples" ]
}

powered_ddc_any_acked() {
    stage=$1
    [ "$(powered_ddc_complete_count "$stage")" -gt 0 ]
}

powered_ddc_mixed() {
    stage=$1
    samples=$(powered_ddc_sample_count "$stage")
    complete=$(powered_ddc_complete_count "$stage")
    [ "$complete" -gt 0 ] && [ "$complete" -lt "$samples" ]
}

powered_ddc_partial() {
    stage=$1
    [ "$(powered_ddc_partial_count "$stage")" -gt 0 ]
}

preload_ddc_summary() {
    bus_hex=$(printf '%s\n' "$boot_log" |
        sed -n 's/.*DDC_DIAG: DAC_POWERED_DDC .* bus=\([[:xdigit:]]\{4\}\) .*/\1/p' |
        head -n 1)

    if [ -z "$bus_hex" ]; then
        printf 'NO TRACE SAMPLE'
        return
    fi

    bus_num=$((16#$bus_hex))
    line=$(printf '%s\n' "$boot_log" |
        awk -v bus="$bus_num" '''
            index($0, "DDC_DIAG: DAC_POWERED_DDC ") { exit }
            $0 ~ ("i2c_write: i2c-" bus " #0 .*a=050") { state = 1; next }
            state == 1 && $0 ~ ("i2c_read:[[:space:]]+i2c-" bus " #1 .*a=050") { state = 2; next }
            state == 2 && $0 ~ ("i2c_result: i2c-" bus " n=2 .*ret=-?[0-9]+") {
                candidate = $0
                state = 0
                next
            }
            $0 ~ ("i2c_(write|read|result): i2c-" bus " ") && state != 0 { state = 0 }
            END { if (candidate != "") print candidate }
        ''' || true)
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
power_samples=$(powered_ddc_sample_count power-on)
load_samples=$(powered_ddc_sample_count load-sense)
power_all_ack=0
load_all_ack=0
power_any_ack=0
load_any_ack=0
partial_response=0
mixed_response=0

if powered_ddc_all_acked power-on; then power_all_ack=1; fi
if powered_ddc_all_acked load-sense; then load_all_ack=1; fi
if powered_ddc_any_acked power-on; then power_any_ack=1; fi
if powered_ddc_any_acked load-sense; then load_any_ack=1; fi
if powered_ddc_partial power-on || powered_ddc_partial load-sense; then partial_response=1; fi
if powered_ddc_mixed power-on || powered_ddc_mixed load-sense; then mixed_response=1; fi

if [ "$power_samples" -eq 0 ] && [ "$load_samples" -eq 0 ]; then
    echo 'No DAC-powered DDC diagnostic samples were found in this boot log.'
    echo 'Result is inconclusive: verify that the --diag-dac-ddc DKMS build is installed and that analog load detection ran.'
elif [ "$power_samples" -eq 0 ] || [ "$load_samples" -eq 0 ]; then
    echo 'Only one DAC diagnostic phase is present in the boot log.'
    echo 'Result is inconclusive: do not treat a missing phase as a failed DDC probe; preserve the full log and verify the diagnostic path ran to completion.'
elif [ "$mixed_response" -eq 1 ]; then
    echo 'Repeated DAC diagnostic samples are mixed: at least one phase both succeeded and failed across attempts.'
    echo 'The hardware state affects DDC behavior, but it is not stable enough for a strong prerequisite conclusion; preserve ordering and repeat before patching behavior.'
elif [ "$power_all_ack" -eq 1 ] && [ "$load_all_ack" -eq 1 ]; then
    echo 'DDC ACK is stable immediately after entering the non-normal DAC load-detect power state and remains stable during active load sense.'
    echo 'The non-normal DAC load-detect power state is strongly implicated; next isolate the minimal prerequisite needed before normal EDID probing.'
elif [ "$power_any_ack" -eq 0 ] && [ "$load_all_ack" -eq 1 ]; then
    if [ "$partial_response" -eq 1 ]; then
        echo 'The immediate phase never completed both messages, but at least one immediate probe was partial; the load-sense phase completed consistently.'
    else
        echo 'DDC does not complete immediately after entering the non-normal DAC load-detect power state but does complete consistently while load sense is active after the settling interval.'
    fi
    echo 'Active load sense and/or its settling interval is implicated more strongly than the non-normal DAC power state alone.'
elif [ "$power_all_ack" -eq 1 ] && [ "$load_any_ack" -eq 0 ]; then
    echo 'DDC completes consistently immediately after entering the non-normal DAC load-detect power state but never completes during the later active-load-sense phase.'
    echo 'The non-normal DAC power state changes DDC behavior, but the later loss is inconsistent with a simple persistent prerequisite; repeat before changing driver behavior.'
elif [ "$partial_response" -eq 1 ]; then
    echo 'At least one DAC diagnostic probe completed one of the two I2C messages (ret=1), but no stable two-message pattern was established.'
    echo 'That differs from total ENXIO and keeps the DAC/load-sense-state hypothesis viable; preserve the full trace before drawing a root-cause conclusion.'
else
    echo 'Neither load-detect-state phase received a complete two-message DDC response.'
    echo 'The DAC/load-sense hypothesis is weakened; known pad_x, GPIO31, and e1b8 theories are already statically weak.'
fi

echo '=== relevant boot log ==='
printf '%s\n' "$boot_log" |
    grep -Ei 'DDC_DIAG|i2c_result|nouveau|edid|ddc|load.detect' |
    tail -n 150 || true
