# NVIF lifetime runtime harness: CPU-only validation

This checkpoint hardens the capture and comparison tooling only. It did not
launch MPV, FFmpeg, or another GPU workload; install a DSO or kernel module;
change the loaded module; update initramfs; or reboot.

## Pinned reproducer and evidence limits

The runtime profile SHA-256 is
`697f1192da6c8e02cd63cbcad89dc2ba7453d0dff79990b9dc170ad951693438`.
It records the exact historical GDB command and the MPV argv, the pinned media
SHA-256, the 18-second historical outer deadline, and the stage-4 BSP
`-EEXIST` termination condition. The saved GDB log does not record the exact
failure timestamp. The historical working directory and actual Xauthority
value also were not saved; the controlled run pins `/home/keivan` for `HOME`
and working directory and hashes the current Xauthority file.

## Capture and comparison protections

- The capture runner refuses to launch unless explicitly passed `--execute`.
- It records the actual boot ID, kernel release, loaded Nouveau srcversion,
  disabled `diag_ctxsw`, media and DSO hashes, resolved MPV and `systemd-cat`
  binary hashes, normalized child environment, and journal cursor.
- It takes the cursor before the whole-boot snapshot, verifies that snapshot
  belongs to the current boot, contains visible kernel transport records, and
  has no hard-stop signatures.
- It archives and hashes the full preflight journal and the cursor-bounded
  delta. The parser rechecks both journal artifacts, their hashes, boot IDs,
  environment, binary identities, and the selected DSO symlink/hash.
- The process group stops on the first BSP NEW `-EEXIST`, kernel hard stop,
  monitor error, or 18-second deadline, followed by passive 30-second journal
  observation. A hard stop overrides an NVIF lifecycle result.
- The hard-stop classifier includes `PRIV_VIOLATION`, `SIGBUS`/`Bus error`, GPU
  reset messages, CTXSW, BAR2/PTE, channel kill, failed-idle, sanitizer, lockdep,
  and PROP RT-overrun records. Capture CLI exit codes now distinguish
  inconclusive A, timeout/boundary failure, and kernel or unexplained workload
  failure; zero only means the capture artifact is usable for pair comparison.
- A's deliberate nonzero termination is accepted only when the termination
  reason records the expected BSP `-EEXIST`. A must reproduce the complete
  wrong-fd/stale-key/duplicate chain before B can count as a candidate result.
  An NVKM duplicate without client identity remains inconclusive.
- On this kernel `diag_ctxsw` is mode `0600`; the runner uses a noninteractive
  read-only `sudo -n /usr/bin/cat` fallback for that parameter only. MPV itself
  is never run as root. If that read is unavailable, the runner refuses before
  launching the workload.

## Validation performed

The complete Mesa lifetime diagnostic package suite passed **64/64** tests
with no skips, using the pristine pinned Mesa source at
`/home/keivan/.cache/nouveau-mesa-26.0.8-1ubuntu0.3/source/mesa-26.0.8/src/gallium/winsys/nouveau/drm/nouveau.c`.
The separate NVIF delete-fd source suite passed **9/9** tests, and the kernel
NVIF duplicate diagnostic source suite passed **2/2** tests, both without
skips. Python syntax compilation passed for the parser, capture runner, and
their test files. `git diff --check` passed.

These are CPU-only source/tool tests. They establish no runtime A/B result,
BAR2/PTE fix, visible playback, or release acceptance. `main` remains HOLD.
