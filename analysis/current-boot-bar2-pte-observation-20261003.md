# Current-boot BAR2/PTE observation

Observation captured on 2026-10-03 while doing CPU-only validation and saved-artifact inspection. This is a kernel event record, not a player result or a causal attribution.

## Event identity

- Boot ID: `8a436489-d363-4749-a3e0-2f134e34931a`
- Kernel: `7.0.0-34-generic`
- Loaded Nouveau srcversion: `354DCD95A5804DE00E20EFB`
- `modinfo`-selected module: `/lib/modules/7.0.0-34-generic/updates/dkms/nouveau.ko.zst`
- Selected module SHA-256: `88abb555831095d6a71ff4db6378c3d3ec2ee13cd99a153e4eaff31397820a39`
- Selected module vermagic: `7.0.0-34-generic SMP preempt mod_unload modversions`
- The loaded srcversion matches the selected file's `modinfo` srcversion. This is the older `354DCD95A5804DE00E20EFB` diagnostic module, not the reviewed validation module with srcversion `29C4D0E409ABB2711FC9A10`.

The journal contains one exact match for the BAR2/HOST_CPU/PTE baseline signature. Running `bar2_baseline.py --require-zero` against the current boot's kernel journal returned exit status `2`:

```text
BAR2_HOST_CPU_PTE_COUNT=1
nouveau 0000:01:00.0: fifo: fault 00 [READ] at 00000000005ea000 engine 05 [BAR2] client 07 [HUB/HOST_CPU] reason 02 [PTE] on channel -1 [00ffbb7000 unknown]
BAR2_BASELINE=CONTAMINATED
```

Journal fields:

```text
_TRANSPORT=kernel
_BOOT_ID=8a436489d3634749a3e02f134e34931a
__REALTIME_TIMESTAMP=1791038529643942  (2026-10-03 07:42:09.643942 PDT)
__MONOTONIC_TIMESTAMP=71315717737      (71315.717737 seconds)
_PID, _COMM, _EXE, _CMDLINE: absent
```

The fault reports channel `-1` and an unknown channel address. The journal provides no process identity, so its producer and relationship to any user-space or Nouveau work remain unknown. The surrounding one-minute system journal query contained this kernel line only. A later process scan found no tracked `mpv`, `ffmpeg`, `ffplay`, `vlc`, or `vainfo` process; that scan was not simultaneous with the event and does not identify its cause.

## Interpretation limits

- This event contaminates the current boot's BAR2/PTE baseline. No player or VA/GPU workload was launched by this CPU-only continuation.
- The same boot also contains prior `CTXSW_TIMEOUT`, channel-killed, failed-to-idle, and PROP/RT-overrun records, so it is unsuitable for a playback experiment regardless of the single PTE event.
- The journal contains many `NOUVEAU_DIAG_BAR2_MAP` map/destroy trace records. Those are diagnostic lifecycle messages and are not counted as BAR2/PTE faults by the exact baseline matcher.
- This observation does not prove that BAR2 caused the MPV or ffplay failure, and it does not establish a BAR2 production fix.
- The review-branch v1-v8 plus duplicate-layer diagnostic module is not loaded on this boot.

## Bounded BAR2 lifecycle correlation

A read-only query of this boot's kernel journal selected
`NOUVEAU_DIAG_BAR2_MAP` records and compared their monotonic timestamps with
the PTE event at `71315717737` usec (`0x5ea000`). There were **544** such
lifecycle records in the captured journal and **zero within +/-120 seconds**
of the PTE.

The nearest preceding lifecycle record was:

```text
mono_usec=54751563175 delta_usec=-16564154562
stage=destroy pages=0x3 object=0000000003d7c343
vma_start=0x0000000000377000 vma_size=0x000000000002a000
backing_addr=0x00000000ff657000 backing_size=0x000000000002a000
backing_target=2 map=000000006922316d maps=0
```

This record is 4h36m04.154562s before the PTE. No later
`NOUVEAU_DIAG_BAR2_MAP` record appears in the captured current-boot kernel
journal. Across all 544 records, neither a recorded backing range
`[backing_addr, backing_addr + backing_size)` nor VMA range
`[vma_start, vma_start + vma_size)` contains `0x5ea000`.

The numeric `map=000000006922316d` value appears in two distinct map/destroy
pairs before the PTE: one for object `000000002b06330a` and a later one for
object `0000000003d7c343`. Both pairs end with `stage=destroy` and `maps=0`.
This demonstrates that the numeric map value alone does not identify one
object lifetime. It does not link either earlier lifecycle to the later PTE.

Result: **no logged BAR2 mapping candidate correlates with this PTE in the
bounded window or by recorded address range**. The diagnostic mapping trace
ends hours before the fault, so this is missing temporal coverage, not proof
that the fault had no mapping/lifetime cause. The channel remains `-1` and
the producer remains unknown.

## CPU-only checks in the same continuation

- BAR2 matcher and detached supervisor: `19/19` unittest cases passed.
- Exact-PTS NV12 comparator: `8/8` unittest cases passed. The available timestamped hardware captures still do not cover the reported approximately 530.083-second scene.
- Detached supervisor synthetic self-test passed: journal-trigger classification and process-tree cleanup.
- `detached_mpv_supervisor.py start` without `--arm` returned `2` and printed its refusal. No detached player service was started.
- The saved user screenshot is coherent and shows no obvious macroblock grid, but it is one still and does not resolve intermittent pixelation.

## Desktop and test gate

From this service's current view, `DISPLAY=:0` answers as X.Org but `xwininfo -root -tree` reports zero child windows. The only X server process visible to `ps` is an `Xvfb`; the user logind sessions are remote TTY sessions without a display. This does not establish a user-visible desktop for playback confirmation.

Do not run another GPU workload from this service. Any future test still needs a logged-in visible desktop, the intended reviewed module identity, and a fresh boot with a zero BAR2/PTE baseline and no hard-stop records.
