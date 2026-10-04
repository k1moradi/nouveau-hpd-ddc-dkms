# Current-boot BAR2/PTE recheck

## Observation

A later read-only check on boot `85152730-d7e1-49d9-b65d-00d01dbdada5`
found that the earlier zero-count baseline no longer held. The detached
supervisor returned `PREFLIGHT_FAIL`, `RUN_ELIGIBLE=false`, with one
BAR2/HOST_CPU/PTE hard stop. The loaded Nouveau module is still the old
`354DCD95A5804DE00E20EFB` build; the reviewed diagnostic module is not loaded.

The matching current-boot kernel record is:

```text
_TRANSPORT=kernel
__MONOTONIC_TIMESTAMP=7848885122
__REALTIME_TIMESTAMP=1791081404831609
nouveau 0000:01:00.0: fifo: fault 00 [READ] at 0000000000325000 engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE] on channel -1 [00ffbb7000 unknown]
```

No other kernel-journal record was returned within two seconds of this
fault. This is an unattributed BAR2/HOST_CPU/PTE observation. The record does
not identify a process, allocation, VMA, or video workload, and `channel -1`
does not identify a former channel owner.

## Preflight and limits

Command:

```sh
cd /home/keivan/nouveau-hpd-ddc-dkms/validation/detached-mpv-supervisor
PYTHONDONTWRITEBYTECODE=1 python3 detached_mpv_supervisor.py check-only --no-desktop
```

Result:

```text
kernel=7.0.0-34-generic
loaded_srcversion=354DCD95A5804DE00E20EFB
BAR2_HOST_CPU_PTE_COUNT=1
PREFLIGHT_FAIL
RUN_ELIGIBLE=false
error=clean-boot BAR2/HOST_CPU/PTE baseline required; found 1
error=clean-boot hard-stop baseline required; found 1
```

The service remained headless (`DISPLAY` unset). No VA/GPU workload was
started, no module or package was changed, no initramfs was changed, and no
reboot was initiated. This recheck is not a v3 diagnostic result and supplies
no attribution to MPV, ffplay, X, or a BAR2 mapping. The boot is ineligible
for hardware testing. `main` remains HOLD.
