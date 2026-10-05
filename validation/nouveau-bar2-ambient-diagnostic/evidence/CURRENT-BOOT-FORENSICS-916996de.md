# Reviewed-module ambient BAR2 faults: boot 916996de

Status: **CONTAMINATED; read-only forensics only.** No selftest debugfs node,
VA probe, player, or controlled GPU workload was run on this boot.

## Provenance and retained inputs

- Boot ID: `916996de-2805-4cf1-9dd1-abb2a60380f8`
- Kernel: `7.0.0-34-generic`
- Loaded/selected Nouveau srcversion: `936407678F3DA1E8515F5EC`
- Reviewed uncompressed module SHA-256:
  `0fefbe31e4b75206670c0a50006f1cc0ca4524a5211420cdd0c16a8cf649f9c8`
- Initial admission-stop journal SHA-256:
  `2495fc0e484aa397af04c0336f0777bb30fc9c40dc4fa1897d53603b2285c1b3`
- Expanded same-boot whole-system journal SHA-256:
  `840f0fcea5f47d307a5764b60b03dd590d3ab2612f4c7853062cd11fe956eacd`

The initial stop archive contains the three faults listed below. The expanded
same-boot archive contains 30 matched raw-register/decoded fault pairs: 25 at
`0x46f000`, five at `0x41f000`. Its single INST value is `0x00ffbb7000`. The
equal INST and repeated VAs do **not** establish object or lifetime identity.
Across all 30 raw tuples, the only field variation is `valo`, reflecting the two
observed VA values (`0x46f000` and `0x41f000`); `unit`, `inst`, `vahi`, and
`type` remain the same. This is register-value recurrence only.

## Initial three fault records

| # | Raw mono usec | Raw realtime usec | Decoded mono usec | Decoded realtime | VA | INST | Decoded tuple |
|---|---:|---:|---:|---|---|---|---|
| 1 | `867460045` | `1791125726363962` | `867461158` | `2026-10-04 07:55:26.365075 -07:00` | `0x46f000` | `0x00ffbb7000` | READ, engine 05 BAR2, client 07 HUB/HOST_CPU, reason 02 PTE, channel -1 unknown |
| 2 | `1320024161` | `1791126178928078` | `1320024790` | `2026-10-04 08:02:58.928707 -07:00` | `0x46f000` | `0x00ffbb7000` | READ, engine 05 BAR2, client 07 HUB/HOST_CPU, reason 02 PTE, channel -1 unknown |
| 3 | `1320505140` | `1791126179409057` | `1320505727` | `2026-10-04 08:02:59.409644 -07:00` | `0x46f000` | `0x00ffbb7000` | READ, engine 05 BAR2, client 07 HUB/HOST_CPU, reason 02 PTE, channel -1 unknown |

For all three raw lines the register tuple is exactly:

```text
unit=05 inst=000ffbb7 valo=0046f000 vahi=00000000 type=00000742
```

Thus the three raw tuples match, apart from their journal timestamps. This is
register-value recurrence only, not evidence of a shared object or lifetime.
The raw and decoded records are retained with their journal metadata and line
numbers in `CURRENT-BOOT-FORENSICS-916996de.json`.

Faults 2 and 3 are `480937` microseconds apart. Between the decoded records,
the expanded journal contains only the raw diagnostic line for fault 3. It has
no explicit BAR2 reset/recovery, map lifetime, channel lifecycle, display, or
power record in that interval. Patch 0010 does not emit reset brackets, so the
absence of a reset line cannot establish that no recovery occurred. The later
sudo initramfs inspection is after these faults and is not a precursor.

## Bounded journal windows

The initial three faults were checked in the whole **system** journal, using
`__MONOTONIC_TIMESTAMP` and ±5-second windows. No Xorg/display, DPMS,
connector/hotplug, runtime-PM, suspend/resume, compositor, udev, channel, or
other Nouveau lifecycle event repeated as a precursor. The keyword scan over
all 30 faults in the expanded archive likewise found zero non-fault events in
those categories within ±5 seconds. This does not prove those subsystems were
inactive; it says the journal contains no such candidate event in the bounded
windows. No ±30-second expansion was warranted by a repeatable ±5-second
candidate.

The raw and decoded fault tuple records are kernel transport. Other journal
metadata, including monotonic/realtime timestamps, transport, PID/TID, comm,
exe, unit, identifier, and message where present, is retained in the JSON
forensic artifact. Missing timestamp data is not replaced with realtime
ordering.

## Source-proven boundary and current attribution

The reviewed fault logger records register values already read by the
interrupt path. BAR2 recovery is called before the live runlist instance
lookup. At that lookup point, `channel -1 [unknown]` means no currently listed
channel matched the reported instance. It does not identify a stale video
channel or any process.

This boot has no patch-0011 ambient mapping records. Running the new offline
correlator over the expanded archive therefore returns
`NO_OBSERVED_BAR2_MAPPING_MATCH` for all 30 faults. That result is expected from
missing instrumentation and is not evidence of an untracked user, absent
mapping, or global absence.

No owner, process, video involvement, v3 involvement, display involvement,
reset causality, teardown causality, or production fix is established.

## Offline ambient-correlator output

The separate ambient correlator was run on the expanded whole-system archive;
this boot has no `NOUVEAU_DIAG_BAR2_MAP` or `NOUVEAU_DIAG_BAR2_RESET` records.
It reports 30 `NO_OBSERVED_BAR2_MAPPING_MATCH` outcomes, zero mapping records,
and zero reset records. This is a tooling-boundary result because patch 0011
was not loaded on this boot; it does not establish that no mapping existed.

- Correlation JSON:
  `CURRENT-BOOT-CORRELATION-916996de.json`, SHA-256
  `26485059be27544ece597c08a1f1aa6df40bff2b93931108b97f32b54516e3c6`
- Correlator source SHA-256:
  `3210982714d24ef0562d9daa800144da0b6b2a5dfeec4655295050e2a9ec6cfb`
- Input whole-system journal SHA-256:
  `840f0fcea5f47d307a5764b60b03dd590d3ab2612f4c7853062cd11fe956eacd`
