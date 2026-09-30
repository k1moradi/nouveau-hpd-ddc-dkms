#!/usr/bin/env bash
set -Eeuo pipefail

NAME=nouveau-hpd-ddc
VERSION=0.1.13-diag1
EXPECTED_KERNEL=7.0.0-34-generic
EXPECTED_BASE_SRCVERSION=57AE1B168D50DB546CD87A1
EXPECTED_DIAG_SRCVERSION=B19B8AAE48467545E652509
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
if [[ $kernel != "$EXPECTED_KERNEL" ]]; then
    echo "FAIL: expected kernel $EXPECTED_KERNEL, found $kernel" >&2
    exit 1
fi
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
    echo "FAIL: disk srcversion is not the diagnostic build" >&2
    exit 1
}
grep -q 'installed' <<<"$dkms_status" || {
    echo "FAIL: DKMS does not report $NAME/$VERSION installed" >&2
    exit 1
}

params=$(modinfo -p "$module_path")
grep -q '^diag_fence_wait:' <<<"$params" || {
    echo "FAIL: diag_fence_wait is absent from module metadata" >&2
    exit 1
}
grep -q '^diag_ctxsw:' <<<"$params" || {
    echo "FAIL: diag_ctxsw is absent from module metadata" >&2
    exit 1
}

loaded_srcversion=$(cat /sys/module/nouveau/srcversion 2>/dev/null || true)
printf 'loaded_srcversion=%s\n' "${loaded_srcversion:-unavailable}"
if [[ $mode == pre-reboot ]]; then
    [[ $loaded_srcversion == "$EXPECTED_BASE_SRCVERSION" ]] || {
        echo "FAIL: the running module changed before reboot" >&2
        exit 1
    }
    echo "PASS: diagnostic is installed on disk; running baseline remains loaded."
    exit 0
fi

[[ $loaded_srcversion == "$EXPECTED_DIAG_SRCVERSION" ]] || {
    echo "FAIL: diagnostic module is not loaded" >&2
    exit 1
}
for parameter in diag_fence_wait diag_ctxsw; do
    parameter_path="/sys/module/nouveau/parameters/$parameter"
    [[ -r $parameter_path ]] || {
        echo "FAIL: loaded parameter is absent: $parameter_path" >&2
        exit 1
    }
    value=$(cat "$parameter_path")
    printf '%s=%s\n' "$parameter" "$value"
    [[ $value == 0 || $value == N ]] || {
        echo "FAIL: $parameter must be false before explicit enablement" >&2
        exit 1
    }
done
echo "PASS: diagnostic module is loaded and both opt-in parameters are disabled."
