#!/bin/bash
set -u

NAME=nouveau-hpd-ddc
VER=0.1.12
k=$(uname -r)
dac_ddc_diag_enabled=0
if [ -f "/usr/src/$NAME-$VER/diagnostic-dac-ddc.enabled" ]; then
    dac_ddc_diag_enabled=1
fi
pnvio_hw_ddc_diag_enabled=0
if [ -f "/usr/src/$NAME-$VER/diagnostic-pnvio-hw-ddc.enabled" ]; then
    pnvio_hw_ddc_diag_enabled=1
fi
board_pad_post_diag_enabled=0
if [ -f "/usr/src/$NAME-$VER/diagnostic-board-pad-post.enabled" ]; then
    board_pad_post_diag_enabled=1
fi

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
nouveau_config=""
nouveau_config_readable=0
if nouveau_config=$(cat /sys/module/nouveau/parameters/config 2>/dev/null); then
    nouveau_config_readable=1
fi
ack_slot_lines=$(printf '%s\n' "$boot_log" |
    grep -F 'DDC_DIAG: ACK_SLOT ' |
    grep -F 'addr=50 ' || true)
ack_slot_count=$(printf '%s\n' "$ack_slot_lines" |
    awk 'NF { count++ } END { print count + 0 }')
firmware_edid_lines=$(printf '%s\n' "$boot_log" |
    grep -F 'DDC_DIAG: FIRMWARE_EDID' || true)
firmware_edid_count=$(printf '%s\n' "$firmware_edid_lines" |
    awk 'NF { count++ } END { print count + 0 }')
d014_init_lines=$(printf '%s\n' "$boot_log" |
    grep -E 'DDC_DIAG: D014_INIT |DDC_DIAG: BOOT0=.*D014_BEFORE=.*D014_AFTER=' || true)
d014_init_count=$(printf '%s\n' "$d014_init_lines" |
    awk 'NF { count++ } END { print count + 0 }')
d014_matrix_lines=$(printf '%s\n' "$boot_log" |
    grep -F 'DDC_DIAG: D014_MATRIX ' || true)
d014_matrix_count=$(printf '%s\n' "$d014_matrix_lines" |
    awk 'NF { count++ } END { print count + 0 }')
board_pad_post_lines=$(printf '%s\n' "$boot_log" |
    grep -F 'DDC_DIAG: BOARD_PAD ' || true)
board_pad_post_decision_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=devinit-post-decision ' || true)
board_pad_post_raw_true_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=devinit-post-decision .*raw_post=1' || true)
board_pad_post_call_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=devinit-post-call ' || true)
board_pad_post_execute_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=devinit-post-call execute=1' || true)
board_pad_gpio_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=gpio31-script ' || true)
board_pad_gpio_execute_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=gpio31-script .*execute=1' || true)
board_pad_gpio_skip_count=$(printf '%s\n' "$board_pad_post_lines" |
    grep -c 'phase=gpio31-script .*execute=0' || true)
pnvio_hw_ddc_lines=$(printf '%s\n' "$boot_log" |
    grep -F 'DDC_DIAG: PNVIO_HW_DDC ' || true)
pnvio_hw_ddc_start_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=start ' || true)
pnvio_hw_ddc_result_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=result ' || true)
pnvio_hw_ddc_400_attempt_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=attempt attempt=primary rate_khz=400 ' || true)
pnvio_hw_ddc_100_attempt_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=attempt attempt=(primary|retry100) rate_khz=100 ' || true)
pnvio_hw_ddc_recovery_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=recovery rate_khz=100 ' || true)
pnvio_hw_ddc_recovery_success_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=recovery rate_khz=100 result=1 ' || true)
pnvio_hw_ddc_caller_init_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=caller-init ' || true)
pnvio_hw_ddc_caller_recovery_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=caller-recovery ' || true)
pnvio_hw_ddc_caller_recovery_success_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=caller-recovery result=1 ' || true)
pnvio_hw_ddc_probe_start_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=start .*length=1 ' || true)
pnvio_hw_ddc_probe_success_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=result ret=2 bytes=1 chunks=1 ' || true)
pnvio_hw_ddc_success_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=result ret=2 bytes=128 chunks=32 ' || true)
pnvio_hw_ddc_software_start_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=software-fallback path=' || true)
pnvio_hw_ddc_software_result_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -c 'phase=software-fallback result ' || true)
pnvio_hw_ddc_software_probe_success_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=software-fallback result ret=2 bytes=1 ' || true)
pnvio_hw_ddc_software_edid_success_count=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Ec 'phase=software-fallback result ret=2 bytes=128 ' || true)
pnvio_hw_ddc_restore_line=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -E 'phase=result .*d004_restored=.*d008_restored=.*d010_restored=.*d014_restored=' |
    tail -n 1 || true)
pnvio_hw_ddc_first_start=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -F 'phase=start bus=d014 ' | head -n 1 || true)
pnvio_hw_ddc_selector=$(printf '%s\n' "$pnvio_hw_ddc_first_start" |
    sed -n 's/.* selector=\([0-9][0-9]*\) .*/\1/p')
pnvio_hw_ddc_first_sequence=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    awk '
        !capture && index($0, "phase=start bus=d014 ") { capture = 1 }
        capture { print; records++ }
        capture && index($0, "phase=result ") { exit }
        records >= 100 { exit }
    ')
pnvio_hw_ddc_unsupported_lines=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -F 'path=bitbang reason=unsupported-shape ' || true)
pnvio_hw_ddc_transfer_lines=$(printf '%s\n' "$pnvio_hw_ddc_lines" |
    grep -Fv 'path=bitbang reason=unsupported-shape ' || true)

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

echo '=== ACK-slot diagnostic ==='
if [ "$nouveau_config_readable" -eq 1 ]; then
    printf 'active nouveau config: %s\n' "$nouveau_config"
else
    echo 'active nouveau config: UNAVAILABLE (not readable by this user; kernel logs may confirm options)'
fi
case "$nouveau_config" in
    *NvI2C=1*) echo 'internal Nouveau I2C path: ACTIVE' ;;
    '')
        if [ "$ack_slot_count" -gt 0 ]; then
            echo 'internal Nouveau I2C path: CONFIRMED by ACK_SLOT samples'
        elif [ "$nouveau_config_readable" -eq 0 ]; then
            echo 'internal Nouveau I2C path: UNKNOWN (the active config could not be read)'
        else
            echo 'internal Nouveau I2C path: NOT ACTIVE (expected NvI2C=1)'
        fi
        ;;
    *)
        if [ "$ack_slot_count" -gt 0 ]; then
            echo 'internal Nouveau I2C path: CONFIRMED by ACK_SLOT samples'
        else
            echo 'internal Nouveau I2C path: NOT ACTIVE (expected NvI2C=1)'
        fi
        ;;
esac
printf '0x50 ACK-slot samples: %s\n' "$ack_slot_count"
if [ "$ack_slot_count" -eq 0 ]; then
    echo 'No samples found; the run is inconclusive. Confirm NvI2C=1 is active and the ACK-slot DKMS build is loaded.'
else
    if [ "$ack_slot_count" -gt 80 ]; then
        echo 'Showing the latest 80 samples:'
        printf '%s\n' "$ack_slot_lines" | tail -n 80
    else
        printf '%s\n' "$ack_slot_lines"
    fi
    echo 'For each sample, SCL is bit 4 and SDA is bit 5; under the documented input interpretation, SCL high with SDA low is consistent with an ACK. External-pad semantics remain unvalidated.'
fi

echo '=== GK104 PNVIO hardware DDC diagnostic ==='
if [ "$nouveau_config_readable" -eq 1 ]; then
    case "$nouveau_config" in
        *NvI2CHw=1*) echo 'active NvI2CHw option: ACTIVE' ;;
        *) echo 'active NvI2CHw option: NOT ACTIVE (expected config=NvI2CHw=1)' ;;
    esac
else
    if [ -n "$pnvio_hw_ddc_lines" ]; then
        echo 'active NvI2CHw option: CONFIRMED by PNVIO_HW_DDC dispatcher logs'
    else
        echo 'active NvI2CHw option: UNAVAILABLE (sysfs config is unreadable and no dispatcher logs were found)'
    fi
fi
printf 'diagnostic DKMS marker: %s\n' "$pnvio_hw_ddc_diag_enabled"
printf 'hardware transfer starts: %s\n' "$pnvio_hw_ddc_start_count"
printf 'hardware transfer results: %s\n' "$pnvio_hw_ddc_result_count"
printf '400 kHz hardware attempts: %s\n' "$pnvio_hw_ddc_400_attempt_count"
printf '100 kHz hardware attempts: %s\n' "$pnvio_hw_ddc_100_attempt_count"
printf '100 kHz recovery attempts/successes: %s/%s\n' \
    "$pnvio_hw_ddc_recovery_count" "$pnvio_hw_ddc_recovery_success_count"
printf 'GOP caller init snapshots: %s\n' "$pnvio_hw_ddc_caller_init_count"
printf 'GOP caller recovery attempts/successes: %s/%s\n' \
    "$pnvio_hw_ddc_caller_recovery_count" \
    "$pnvio_hw_ddc_caller_recovery_success_count"
printf 'one-byte probe starts: %s\n' "$pnvio_hw_ddc_probe_start_count"
printf 'one-byte probe successes: %s\n' "$pnvio_hw_ddc_probe_success_count"
printf 'complete 128-byte GOP reads: %s\n' "$pnvio_hw_ddc_success_count"
printf 'software fallback starts/results: %s/%s\n' \
    "$pnvio_hw_ddc_software_start_count" "$pnvio_hw_ddc_software_result_count"
printf 'software one-byte probe successes: %s\n' \
    "$pnvio_hw_ddc_software_probe_success_count"
printf 'software 128-byte EDID reads: %s\n' \
    "$pnvio_hw_ddc_software_edid_success_count"
if [ -n "$pnvio_hw_ddc_restore_line" ]; then
    printf 'last controller-register restore: %s\n' "$pnvio_hw_ddc_restore_line"
else
    echo 'last controller-register restore: NO SAMPLE'
fi
printf 'DCB selector in first transfer: %s\n' "${pnvio_hw_ddc_selector:-NO SAMPLE}"
if [ "$pnvio_hw_ddc_diag_enabled" -eq 0 ] && [ -z "$pnvio_hw_ddc_lines" ]; then
    echo 'The GK104 hardware DDC diagnostic was not enabled in the installed DKMS source.'
elif [ "$pnvio_hw_ddc_lines" ]; then
    if [ -n "$pnvio_hw_ddc_first_sequence" ]; then
        echo 'First complete controller sequence:'
        printf '%s\n' "$pnvio_hw_ddc_first_sequence"
    fi
    echo 'Showing the latest 80 controller/fallback records:'
    printf '%s\n' "$pnvio_hw_ddc_transfer_lines" | tail -n 80
    if [ "$pnvio_hw_ddc_unsupported_lines" ]; then
        echo 'First unsupported two-message shape (bounded details):'
        printf '%s\n' "$pnvio_hw_ddc_unsupported_lines" | head -n 1
    fi
    if [ "$pnvio_hw_ddc_software_edid_success_count" -gt 0 ]; then
        echo 'The GOP-derived software fallback returned all 128 base-block bytes; inspect checksum_valid and EDID below.'
    elif [ "$pnvio_hw_ddc_software_probe_success_count" -gt 0 ]; then
        echo 'The software fallback completed the one-byte presence probe; DRM should issue the 128-byte read next.'
    elif [ "$pnvio_hw_ddc_success_count" -gt 0 ]; then
        echo 'The hardware controller returned both EDID block-0 messages and all 128 bytes.'
    elif [ "$pnvio_hw_ddc_start_count" -eq 0 ]; then
        echo 'No hardware transfer started; any path=bitbang records show why a transfer used Linux bitbang.'
    else
        echo 'The hardware transfer did not complete as a full 128-byte read; use the result and status above.'
    fi
else
    echo 'No PNVIO hardware samples found; result is inconclusive.'
    echo 'Confirm NvI2CHw=1 was active and that GK104 bus 0 used DCB selector 3.'
fi

echo '=== D014 init and drive/sense diagnostic ==='
if [ "$d014_init_count" -eq 0 ]; then
    echo 'No D014 init snapshot found; confirm --diag-d014-sense or --diag-ibuf was used.'
else
    printf '%s\n' "$d014_init_lines"
fi
printf 'D014 drive/sense samples: %s/32 maximum\n' "$d014_matrix_count"
if [ "$d014_matrix_count" -eq 0 ]; then
    echo 'No matrix samples found; confirm an internal I2C address-0x50 transfer used physical register 0xd014.'
else
    printf '%s\n' "$d014_matrix_lines"
    echo 'Bits 4/5 are documented as SCL_IN/SDA_IN, but their external-pad semantics remain unvalidated.'
fi

echo '=== firmware EDID snapshot ==='
if [ "$firmware_edid_count" -eq 0 ]; then
    echo 'No firmware EDID snapshot records found; confirm --diag-firmware-edid was used and DVI-I detection reached the analog fallback.'
else
    printf '%s\n' "$firmware_edid_lines"
fi

echo '=== GK104 POST / GPIO31 read-only trace ==='
printf 'diagnostic DKMS marker: %s\n' "$board_pad_post_diag_enabled"
printf 'POST decision snapshots: %s (raw_post=1: %s)\n' \
    "$board_pad_post_decision_count" "$board_pad_post_raw_true_count"
printf 'effective nvbios_post calls: %s (execute=1: %s)\n' \
    "$board_pad_post_call_count" "$board_pad_post_execute_count"
printf 'GPIO31 interpreter operations: %s (execute=1: %s, execute=0: %s)\n' \
    "$board_pad_gpio_count" "$board_pad_gpio_execute_count" "$board_pad_gpio_skip_count"
if [ "$board_pad_post_diag_enabled" -eq 0 ] && [ -z "$board_pad_post_lines" ]; then
    echo 'The GK104 POST/GPIO31 trace was not enabled in the installed DKMS source.'
elif [ -z "$board_pad_post_lines" ]; then
    echo 'No trace records found; result is inconclusive. Confirm the DKMS module loaded and the GPU is GK104.'
else
    printf '%s\n' "$board_pad_post_lines" | head -n 80
    echo 'raw_post records the 0x2240c decision before overrides; devinit-post-call records the effective execute argument passed to nvbios_post().'
    echo 'A GPIO31 execute=1 record means the existing interpreter write passes its execution guard; execute=0 means that reached opcode was skipped by script conditions. No GPIO record alone does not prove the conditional script path was absent.'
fi

echo '=== EDID ==='
found_edid=0
for e in /sys/class/drm/card*-DVI-I-*/edid; do
    [ -e "$e" ] || continue
    found_edid=1
    printf '%s: ' "$e"
    stat -c '%s bytes' "$e"
    printf '  header:'
    od -An -tx1 -N16 "$e" 2>/dev/null || true
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
    if [ "$dac_ddc_diag_enabled" -eq 1 ]; then
        echo 'No DAC-powered DDC diagnostic samples were found in this boot log.'
        echo 'Result is inconclusive: verify that analog load detection ran and that the diagnostic module loaded.'
    else
        echo 'The DAC-powered DDC probe was not enabled for this boot; no DAC-powered samples were expected.'
    fi
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
