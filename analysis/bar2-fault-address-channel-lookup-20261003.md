# GK104 BAR2 fault address and channel lookup

## Scope

This is a CPU-only source trace of the saved GK104 fault record:

```text
fault 00 [READ] at 00000000005ea000 engine 05 [BAR2]
client 07 [HUB/HOST_CPU] reason 02 [PTE]
on channel -1 [00ffbb7000 unknown]
```

The source examined is the pinned Linux 7.0 Ubuntu adaptation under
`/home/keivan/nouveau-vaapi-app-validation/master-handoff-build-20261002/source-gitapply-stack-v8-20261002/`.
No kernel code or module was changed by this audit.

## What `0x5ea000` addresses

`gf100_fifo_intr_mmu_fault_unit()` reads the per-unit `inst`, `valo`, `vahi`, and
`type` registers. It decodes `info.addr = ((u64)vahi << 32) | valo` without a
shift, and `info.inst = (u64)inst << 12`; `unit` becomes `info.engine`
(`drivers/gpu/drm/nouveau/nvkm/engine/fifo/gf100.c:755-775`). GK104's tables map
engine value `0x05` to BAR2, client `0x07` to HOST_CPU, and reason `0x02` to PTE
(`.../nvkm/engine/fifo/gk104.c:478-539`). Thus `0x5ea000` is the captured fault
address (`valo` plus `vahi`), while `0x00ffbb7000` is a separate instance-block
address. Neither is inferred from its magnitude.

On GK104, the BAR2 VMM is created from GPU-VA base zero and the PCI BAR2 resource
length, with optional halving controlled by `NvBar2Halve`
(`.../nvkm/subdev/bar/gf100.c:96-119,171`). PCI source selection maps
`NVKM_BAR2_INST` to the BAR2 PCI resource (`.../nvkm/engine/device/pci.c:1563-1599`).
The current K4200 sysfs resource table places BAR2 at resource index 3,
`0xde000000-0xdfffffff`, a 32 MiB aperture. In the direct instmem mapping path,
Nouveau allocates a VMA in this BAR2 VMM and maps the CPU aperture at
`resource_addr(BAR2) + vma->addr`
(`.../nvkm/subdev/instmem/nv50.c:133-177`).

Therefore, for this GK104 path, `0x5ea000` is a **BAR2 VMM virtual address and
aperture-relative offset**. It is not a host pointer or a backing physical
address. It falls within the current 32 MiB aperture, but that range fact alone
does not identify an allocation. The previous retrospective comparison against
`backing_addr` remains weak unless both values are proven to use the same address
domain.

## Why the log says `channel -1 ... unknown`

`gf100_fifo_mmu_fault_recover()` handles a BAR2 fault by calling
`nvkm_bar_bar2_reset()` before resolving the instance address and formatting the
fault line (`.../nvkm/engine/fifo/gf100.c:580-606`). It then calls
`nvkm_chan_get_inst(&fifo->engine, info.inst, ...)`.

`nvkm_chan_get_inst()` searches the applicable FIFO runlists
(`.../nvkm/engine/fifo/chan.c:306-325`). The runlist helper scans allocated/used
CHID entries and returns a channel only when `chan->inst->addr == info.inst`
(`.../nvkm/engine/fifo/runl.c:183-204`). The logger prints `-1` and `unknown`
when that lookup returns `NULL` (`gf100.c:599-606`).

So `channel -1 [00ffbb7000 unknown]` means: **the captured instance-block
address did not exactly match a channel in the live runlist entries examined at
the lookup point**. The lookup is after BAR2 recovery/reset. It does not prove
that a video channel was destroyed, that the instance is stale, or that Xorg,
MPV, FFmpeg, or any other client caused the fault. The current boot after the
reported reboot has no matching BAR2/PTE record; the old record is not a new
reproduction.

## Safe diagnostic boundary

The generic interrupt handler holds `device->intr.lock` while it calls device
interrupt handlers (`.../nvkm/core/intr.c:164-207`); the GK104 FIFO handler
dispatches the MMU-fault handler in that context
(`.../nvkm/engine/fifo/gk104.c:657-711`). The VMM software-node search has no
internal lock (`.../nvkm/subdev/mmu/vmm.c:934-949`), while its normal caller
holds `vmm->mutex.vmm` (`.../nvkm/subdev/mmu/uvmm.c:122-130`). The generic VMM
descriptor exposes page-table mapping/unmapping callbacks, not a generic
read-PTE operation (`.../nvkm/subdev/mmu/vmm.h:68-81`).

Do not add a VMM mutex acquisition or an unlocked tree/PTE walk to the fault
interrupt path. Also do not label a read after `nvkm_bar_bar2_reset()` as a
pre-fault hardware snapshot.

The smallest useful diagnostic design is:

1. If more raw fault evidence is needed, emit the already-captured BAR2 register
   tuple (`unit`, `inst`, `valo`, `vahi`, `type`) in a BAR2-only record before
   entering recovery. This reads no additional register and changes no mapping.
2. In the v3 test's process context, log BAR2 VMM identity and the test VMA start,
   size, and lifecycle stage around map, unmap, remap, and destruction, using the
   existing VMM synchronization contract. This can show whether `0x5ea000` falls
   in the test VMA; it is software VMA evidence, not a raw hardware PTE value.
3. Only add actual PDE/PTE contents after a separate GK104-aware read-side helper
   has a reviewed lock and address-domain contract. No generic helper in this
   source stack provides that read today.

For channel diagnosis, the current `-1` line already reports the failed lookup.
An explicit `lookup=miss` tag could make this clearer, but dumping a list of
channels from interrupt context is not justified by the present evidence.
