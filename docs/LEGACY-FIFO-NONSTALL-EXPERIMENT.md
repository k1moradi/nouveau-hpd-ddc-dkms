# Legacy FIFO nonstall event A/B

## Status

This is an opt-in second-stage experiment for the K4200 VA-API failure. It
keeps the existing GK104 legacy-video context mapping fix and adds only the
legacy FIFO nonstall-event index correction. It is not yet a confirmed K4200
fix.

## Why this experiment is justified

The patch-1-only run removed the original video-channel kill, but the later
core shows a SIGBUS in Mesa's Nouveau VA driver while it copies the old 1 MiB
BSP BO into a mapped replacement BO. At that point `dec->bsp_ptr` is NULL.
The unsigned pointer subtraction then produces a huge rounded allocation
size; the input chunks are only 3 and 9 bytes. This is a downstream failure,
not evidence that the H.264 payload is oversized.

Mesa's `nvc0_decoder_bsp_begin()` maps the BSP BO before setting `bsp_ptr`.
The Nouveau BO-map path waits for pending GPU work through GEM CPU_PREP, whose
fence wait is bounded at 30 seconds. The core does not record the CPU_PREP
return value, so a wait timeout is strongly suggested by the roughly 29.3
second interval between the first relevant GPU fault/trap and SIGBUS, but is
not directly proven.

The matching FIFO source path provides a specific mechanism to test. GK104's
FIFO has no per-runlist `nonstall_ctor`; its interrupt handler notifies the
global nonstall event at index 0. `nvkm_uchan_uevent()` currently registers
the channel's nonstall event using `runl->id` unconditionally. If that index
does not match the global event, fence waits can time out. The upstream
candidate chooses `runl->id` only when a per-runlist nonstall constructor is
present and uses index 0 otherwise.

This links the observed wait window to the exact legacy FIFO event mismatch,
but it does not yet prove that mismatch caused this core. The experiment
changes only that event-index selection; it does not alter the video-context
mapping patch, Mesa, firmware, or the test input.

## Enable the experiment

From the canonical repository:

```bash
pkexec env NOUVEAU_DKMS_JOBS=2 ./install.sh --experimental-legacy-nonstall
```

The option is intentionally exclusive with display/DDC diagnostics. It stages
no module option and performs no hardware-register writes. The DKMS build
continues to include the existing patch-1 context-mapping fix and opts into
the nonstall patch only when this flag is supplied.

After reboot, verify the module path/srcversion and confirm the build result is
`patched` (or `already-fixed` if the kernel source already contains the
equivalent change):

```bash
./verify-after-reboot.sh
```

Then repeat the same VA-API reproduction and capture the kernel log from the
same time window. First test one run; do not start the 20-run reliability
series until the single run completes without the SIGBUS.

To return to the patch-1-only source configuration, run the installer with no
diagnostic or experimental flags:

```bash
pkexec env NOUVEAU_DKMS_JOBS=2 ./install.sh
```

## Interpretation

- Decode completes and the SIGBUS/fence-timeout interval disappears: strong
  evidence that legacy nonstall event registration caused the second-stage
  failure. Then repeat the 900-frame test and inspect for any remaining GPU
  faults before considering the fix validated.
- The same 30-second pause and SIGBUS recur: this patch did not fix the wait
  path; preserve the new log/core evidence and investigate the precise CPU_PREP
  and TTM result next.
- SIGBUS disappears but a PGRAPH trap or another decode failure remains: the
  event-index issue may be one contributing bug, but the remaining engine
  failure is independent and still blocks success.

The pass condition remains actual VA-API hardware decoding to completion, with
no software fallback, channel death, relevant PTE/BAR fault, engine trap,
SIGBUS, or SIGSEGV. A single successful run is not the 20-run reliability
criterion.
