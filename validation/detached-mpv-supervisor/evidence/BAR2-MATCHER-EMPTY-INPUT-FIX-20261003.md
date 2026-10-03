# BAR2 baseline matcher: empty-input fail-closed correction

## Finding

`bar2_baseline.py` parses a supplied log file or stdin; it does not fetch the
kernel journal. During a read-only check, it was invoked with
`--require-zero` and no journal piped to stdin. Empty stdin produced
`BAR2_HOST_CPU_PTE_COUNT=0` and `BAR2_BASELINE=CLEAN`, which was a false clean
result caused by the invocation supplying no evidence. The current-boot journal
was then queried directly and contains the saved BAR2/HOST_CPU/PTE event at
`0x471000`.

The CLI now returns status 3 and prints
`BAR2_BASELINE=UNKNOWN_EMPTY_INPUT` for empty or whitespace-only input. It
continues to return status 2 when `--require-zero` finds a matching event.
Nonempty text with no matching event can pass the text matcher; a real baseline
still requires the caller to supply the complete, visible current-boot kernel
journal. The detached supervisor's preflight performs its own journal
visibility and full-boot checks.

## Validation

Working directory: `/home/keivan/nouveau-hpd-ddc-dkms`

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  validation/detached-mpv-supervisor/tests/test_bar2_baseline.py
```

Result: **7/7 passed, zero skips, exit status 0**. Tests cover the three saved
fault lines, the saved `0x471000` fault, literal-bracket matching, near misses,
empty-input rejection, nonmatching nonempty input, and the contaminated-baseline
exit code.

The full detached-supervisor suite also passed:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=validation/detached-mpv-supervisor \
  python3 -m unittest discover -s validation/detached-mpv-supervisor/tests -v
```

Result: **49/49 passed, zero skips, exit status 0**.

The actual read-only current-boot scan was:

```sh
journalctl -k -b --no-pager -o cat | \
  python3 validation/detached-mpv-supervisor/bar2_baseline.py --require-zero
```

It found one event and exited 2:

```text
BAR2_HOST_CPU_PTE_COUNT=1
nouveau 0000:01:00.0: fifo: fault 00 [READ] at 0000000000471000 engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE] on channel -1 [00ffbb7000 unknown]
BAR2_BASELINE=CONTAMINATED
```

The loaded module remains the old `354DCD95A5804DE00E20EFB` diag4-v3 build on
boot `03f7b214-ea03-4be8-af92-f6a073942e9d`; the reviewed diagnostic stack is
not deployed. This result does not authorize GPU work. No VA/GPU workload,
module operation, install, package operation, or reboot was performed.
