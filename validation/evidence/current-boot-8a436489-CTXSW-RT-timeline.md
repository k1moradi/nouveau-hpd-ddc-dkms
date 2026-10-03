# Current boot Nouveau event timeline

This is a read-only snapshot of kernel journal records from boot
`8a436489-d363-4749-a3e0-2f134e34931a`. It records a temporal sequence in the
existing `diag4-v3` boot. It is not a clean-baseline playback test and does
not establish causation between the graphics traps, CTXSW timeout, channel
kill, or failed-to-idle event.

## Runtime identity at capture

- Kernel: `7.0.0-34-generic`.
- Loaded Nouveau srcversion: `354DCD95A5804DE00E20EFB`.
- Selected module: `/usr/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst`.
- Selected module SHA-256: `88abb555831095d6a71ff4db6378c3d3ec2ee13cd99a153e4eaff31397820a39`.
- Selected module srcversion matches the loaded srcversion above.
- Selected module vermagic: `7.0.0-34-generic SMP preempt mod_unload modversions`.
- `mpv`, `ffplay`, `ffmpeg`, and `vlc` were not running during the read-only
  process check.

This is the old `diag4-v3` module identity, not the reviewed v1-v8 plus
duplicate-layer diagnostic build. Do not use this boot for playback or for
accepting the new diagnostic selftests.

## Journal sequence

The full current-boot kernel journal contains:

| Signature | Records | Time / relationship |
|---|---:|---|
| PROP RT width/height overrun | 67 | One burst from `2026-10-02T19:52:32.053877Z` through `19:52:32.496169Z` (442,292 microseconds). |
| `SCHED_ERROR 0a [CTXSW_TIMEOUT]` | 1 | `19:52:36.982358Z`, 4,486,189 microseconds after the last RT record. |
| Xorg channel 6 killed | 1 | `19:52:36.987068Z`, 4,710 microseconds after the CTXSW record. |
| Xorg channel 9 failed to idle | 1 | `19:53:01.250255Z`, 24,267,897 microseconds after the CTXSW record. |
| BAR2/HOST_CPU/PTE fault | 0 | No matching fault in this boot's journal at capture. |
| `PRIV_VIOLATION` | 0 | No matching record in this boot's journal at capture. |

The timing places the PROP/RT burst before the CTXSW/recovery sequence. It is
temporal association only; this log does not identify the originating draw,
engine state, or a causal edge. The zero BAR2/PTE count does not make the boot
clean because the CTXSW, channel-kill, failed-to-idle, and RT-overrun records
are present.

## Preserved records

`current-boot-8a436489-d363-4749-a3e0-2f134e34931a-nouveau-events.jsonl`
contains the 70 selected journal records above, preserving the journal fields
and messages needed to recheck the counts and timestamp deltas.

```text
SHA-256: fdf8f3d43cd2e2735ac83b037f112d5676b755df258f4db89e7137d9794303bd
```

The snapshot was collected without launching a player or other VA/GPU test,
changing a module, installing packages, updating initramfs, or rebooting.
`main` remains on HOLD.
