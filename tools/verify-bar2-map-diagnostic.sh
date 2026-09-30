#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
VERSION=0.1.13-diag2
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_PREVIOUS_SRCVERSION=B19B8AAE48467545E652509
EXPECTED_DIAG_SRCVERSION=72DEE2B4ECFF77AD3764039
mode=${1:-post-reboot}
SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")

if [[ $EUID -ne 0 ]]; then
    exec sudo -- "$SCRIPT_PATH" "$@"
fi
if [[ $mode != pre-reboot && $mode != post-reboot ]]; then
    echo "Usage: $0 [pre-reboot|post-reboot]" >&2
    exit 2
fi

kernel=$(uname -r)
[[ $kernel == "$EXPECTED_KERNEL" ]] || {
    echo "FAIL: expected kernel $EXPECTED_KERNEL, found $kernel" >&2
    exit 1
}
module_path=$(modinfo -n nouveau)
disk_srcversion=$(modinfo -F srcversion "$module_path")
dkms_status=$(dkms status -m "$NAME" -v "$VERSION" -k "$kernel")
printf 'kernel=%s\nmodule_path=%s\ndisk_srcversion=%s\n' "$kernel" "$module_path" "$disk_srcversion"
printf 'dkms_status=%s\n' "$dkms_status"

[[ $module_path == "/lib/modules/$kernel/updates/dkms/nouveau.ko.zst" ]] || {
    echo "FAIL: nouveau is not selected from the DKMS updates path" >&2
    exit 1
}
[[ $disk_srcversion == "$EXPECTED_DIAG_SRCVERSION" ]] || {
    echo "FAIL: on-disk module is not the reviewed diag2 build" >&2
    exit 1
}
grep -q 'installed' <<<"$dkms_status" || {
    echo "FAIL: DKMS does not report $NAME/$VERSION installed" >&2
    exit 1
}

params=$(modinfo -p "$module_path")
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    grep -q "^$parameter:" <<<"$params" || {
        echo "FAIL: module parameter $parameter is missing" >&2
        exit 1
    }
done

loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
printf 'loaded_srcversion=%s\n' "${loaded_srcversion:-unavailable}"
if [[ $mode == pre-reboot ]]; then
    [[ $loaded_srcversion == "$EXPECTED_PREVIOUS_SRCVERSION" ]] || {
        echo "FAIL: running module changed before reboot" >&2
        exit 1
    }
    for parameter in diag_fence_wait diag_ctxsw; do
        value=$(cat "/sys/module/nouveau/parameters/$parameter")
        printf '%s=%s\n' "$parameter" "$value"
        [[ $value == 0 || $value == N ]] || {
            echo "FAIL: existing diagnostic parameter $parameter is unexpectedly enabled" >&2
            exit 1
        }
    done
    echo "PASS: diag2 is installed on disk; diag1 remains loaded and unchanged."
    exit 0
fi

[[ $loaded_srcversion == "$EXPECTED_DIAG_SRCVERSION" ]] || {
    echo "FAIL: diag2 is not the loaded Nouveau module" >&2
    exit 1
}
grep -Eq '(^| )nouveau\.diag_bar2_map=1( |$)' /proc/cmdline || {
    echo "FAIL: boot command line did not enable early BAR2 mapping tracing" >&2
    exit 1
}
for parameter in diag_fence_wait diag_ctxsw diag_bar2_map; do
    value=$(cat "/sys/module/nouveau/parameters/$parameter")
    printf '%s=%s\n' "$parameter" "$value"
    if [[ $parameter == diag_bar2_map ]]; then
        [[ $value == 1 || $value == Y ]] || {
            echo "FAIL: diag_bar2_map must be enabled from early module load" >&2
            exit 1
        }
    else
        [[ $value == 0 || $value == N ]] || {
            echo "FAIL: $parameter must remain disabled until explicitly enabled" >&2
            exit 1
        }
    fi
done
echo "PASS: diag2 is loaded; BAR2 tracing was enabled at module load, other probes remain off."
