# GK104 ambient BAR2 instmem source audit

Status: **SOURCE-PROVEN** call/lifetime behavior; **HYPOTHESES** below remain
unresolved. This audit uses the pinned Ubuntu 7.0.0-34 Nouveau source archive
SHA-256 `a874e1fb08d2ee695b08e0c8ce6fd2c76a4bf7ffa98882fbabd233380ef8a85a`
with the reviewed patches through 0010 applied. It does not attribute any
current-boot fault to an object or subsystem.

## Hardware family and address domain

The PCI table names device `10de:11b4` Quadro K4200. The GK104/NVE4 device
entry selects `gf100_bar_new` and `nv50_instmem_new` (`nvkm/engine/device/pci.c`,
`nvkm/engine/device/base.c`, `nve4_chipset`). Therefore the applicable BAR2
implementation is GF100, not the older NV50 BAR implementation.

`gf100_bar_oneinit_bar()` creates the BAR2 VMM with start address `0` and
length `resource_size(NVKM_BAR2_INST)` (or half that length when
`NvBar2Halve` is enabled). The fault VAs `0x41f000` and `0x46f000` are inside
that numeric address-space range, subject to the actual resource length. This
makes comparison against an observed `bar->addr` range appropriate. Comparing
against the backing `mem_addr` is not appropriate and is prohibited by the
correlator contract.

## Normal mapping owner and lifetime

The per-allocation owner is `struct nv50_instobj` in
`nvkm/subdev/instmem/nv50.c`. It stores:

- `ram`: referenced backing `nvkm_memory`;
- `bar`: the BAR2 `struct nvkm_vma *`;
- `map`: CPU `ioremap_wc()` mapping into the BAR2 PCI resource;
- `maps`: the active `nvkm_kmap()` access refcount;
- `lru`: whether a zero-ref cached mapping can be evicted.

The BAR2 start and exact mapped length are `iobj->bar->addr` and
`iobj->bar->size`. `nv50_instobj_kmap()` requests
`nvkm_memory_size(&iobj->base.memory)` bytes at 4 KiB page size with
`nvkm_vmm_get(vmm, 12, size, &bar)`, then maps the backing memory with
`nvkm_memory_map()`, stores the VMA, and calls `ioremap_wc()` at
`resource_addr(NVKM_BAR2_INST) + (u32)bar->addr`.

`nvkm_kmap()` and `nvkm_done()` are dispatch macros in
`include/nvkm/core/memory.h`; for an instobj they call
`nv50_instobj_acquire()` and `nv50_instobj_release()`.

- `maps == 0 -> 1`: under the instmem mutex, acquire creates the BAR2 VMA if
  needed, selects fast BAR2 or slow BAR0 accessors, then publishes refcount 1.
- Nested acquire: increments `maps` and reuses the existing access mapping.
- Final release: `wmb()`, BAR flush, and refcount decrement. At zero, the
  object becomes LRU-eligible when it has a fast map. This does **not** unmap
  the VMA, `iounmap()` the CPU address, or clear the VMM PTEs. It clears the
  `memory.ptrs` accessors. Thus map-ref release and VMA teardown are different
  transitions. In correlator output, a match after `LAST_MAP_RELEASE` means
  only that no active kmap reference was logged; it explicitly reports the
  VMA as still cached in the LRU and does not claim teardown. Only a later
  `EVICTED` or verified `DESTROYED` record marks source-observed VMA teardown.
- LRU eviction: the next `nv50_instobj_kmap()` can remove the first unused
  object from the LRU, `iounmap()` its mapping, and call `nvkm_vmm_put()` to
  remove the VMA/PTE mapping.
- Object destruction: `nv50_instobj_dtor()` removes it from LRU, captures
  `map`/`bar`, `iounmap()`s and `nvkm_vmm_put()`s the VMA when BAR2 VMM exists,
  unreferences backing RAM, and removes the instobj from the instmem list.

The existing code does not assert in the destructor that `maps == 0`. Patch
0011 must record `refs` at `DESTROYING` and let the offline parser mark a
nonzero-ref destruction as an invalid/incomplete lifecycle; it must not repair
or conceal the condition.

## BAR2 reset behavior

For this device, `nvkm_bar_bar2_reset()` calls the selected BAR2 `.init()` and
`.wait()` methods. GF100 `.init()` writes register `0x001714` with the BAR2
page-directory instmem address and enable bit. The wait path performs the BAR
flush (twice in `gf100_bar_bar1_wait()`). The reset path does not replace the
software `nvkm_vmm`, traverse instobj allocations, clear `iobj->bar`/`map`,
change `iobj->maps`, or repopulate per-object VMA mappings.

**Not source-proven:** the exact GPU-internal translation-cache/PTE state
invalidated by reprogramming register `0x001714` and flushing is not specified
by this C path. The software tree and per-instobj cached fields can remain
present across the reset call. Whether a live mapping loses required hardware
translation state is H3 and requires the mapping/reset timeline from patch
0011; do not infer it from the function name `reset`.

## Source call graph: normal callers that can enter this mapping path

```text
GK104 device setup
  ├─ GF100 BAR2 VMM + NV50 instmem implementation
  ├─ nvkm_memory_new(target=INST)
  │    └─ nvkm_instobj_new / nv50_instobj_wrap
  ├─ nvkm_gpuobj_ctor (root GPU objects, optional zeroing)
  │    └─ nvkm_kmap(gpuobj) -> nvkm_kmap(instobj memory)
  ├─ GK104 FIFO channel/RAMFC and engine-context writers
  │    └─ nvkm_kmap(chan->inst)
  ├─ GF100 FIFO USERD clear / generic channel instobj paths
  │    └─ nvkm_kmap(userd/instobj memory)
  ├─ GF100 GR context allocation/setup
  │    └─ nvkm_kmap(context instmem)
  ├─ GF100 MMU PTE/PDE writers and BAR2 VMM bootstrap/join
  │    └─ nvkm_kmap(page-table or BAR instmem)
  ├─ Falcon/ACR/FIFO/FB and other NVKM_MEM_TARGET_INST users
  │    └─ common memory or gpuobj accessors
  └─ instmem suspend save / resume load
       └─ nvkm_kmap(instobj)

nvkm_kmap(instobj)
  └─ nv50_instobj_acquire
       ├─ cached fast mapping: maps++
       └─ maps==0: nv50_instobj_kmap
            ├─ nvkm_vmm_get(BAR2 VMM)
            ├─ nvkm_memory_map(backing RAM -> BAR2 VMM PTEs)
            │    └─ GF100 MMU page-table writers
            └─ ioremap_wc(BAR2 resource + low aperture offset)
```

These source paths establish that FIFO, GR, MMU, GPU-object, firmware and
instmem maintenance code can use the mapping. They do **not** establish which
path ran at any particular fault. The K4200 FIFO channel config uses the
shared FIFO USERD memory allocated as `NVKM_MEM_TARGET_INST`; channel clear
and RAMFC/context writes have explicit `nvkm_kmap()` calls. The GR and MMU
paths likewise have explicit direct kmap callsites. A running display
compositor or X server could cause driver work through these paths, but that
runtime participation is an **inference** and is not proven by this source
graph or the current journal.

## Competing hypotheses and required discriminator

| Hypothesis | Observation needed from ambient mapping log | Current status |
|---|---|---|
| H1 active-map PTE failure | Fault VA lies in one `MAP_READY` mapping while the same ID has an active `FIRST_MAP_ACTIVE` interval and no `LAST_MAP_RELEASE`; mapping result is success. | Unresolved; current boot has no ambient lifetime records. |
| H2 stale access after teardown | Fault VA matches an ID's range after `EVICTED` or verified `DESTROYED`, with release-to-fault delta and no active replacement interval. `LAST_MAP_RELEASE` alone is only the kmap-ref edge; its VMA remains cached. | Unresolved. A numeric range match remains non-causal. |
| H3 reset coherence | The same successful active ID/range is present before and after a bracketed BAR2 reset, has no release/recreate, and a later fault falls in it. | Unresolved; C source shows software state survives reset but not the hardware cache effect. |
| H4 initial establishment | Fault time falls inside `MAP_BEGIN` / `MAP_VMA_RESERVED` through `MAP_READY` or `MAP_FAILED`; report whether the VA was known yet. | Unresolved. |
| H5 untracked BAR2 use | No active, transition, cached/released, or destroyed-range record numerically matches the fault in a complete single-boot log. | Unresolved. Absence of a match would not prove global absence if logging is incomplete or the user is outside this instrumented path. |

Patch 0011 therefore needs `ALLOCATED`, `MAP_BEGIN`, `MAP_VMA_RESERVED`,
`MAP_READY`/`MAP_FAILED`, `FIRST_MAP_ACTIVE`, `LAST_MAP_RELEASE`,
`EVICTING`/`EVICTED`, and `DESTROYING`/`DESTROYED` events. The additional
reserve/evict edges are required because mapping setup recursively allocates
page tables and because the final `nvkm_done()` leaves a cached VMA in the
LRU. BAR2 reset begin/end records are needed to test H3. All allocation and
mapping records are emitted from process-context paths; the IRQ path remains
limited to the existing register snapshot and existing recovery behavior.

## Fault-path execution context and reset-marker perturbation

The source call path reaches `gf100_fifo_intr_mmu_fault_unit()` from
`gf100_fifo_intr_mmu_fault()`, called synchronously by the Nouveau interrupt
dispatcher registered with `request_irq()`. That handler reads the four MMU
fault registers once and emits the existing raw tuple before calling
`nvkm_fifo_fault()`. `gf100_fifo_mmu_fault_recover()` then calls
`nvkm_bar_bar2_reset()` for a BAR2/instmem engine before `nvkm_chan_get_inst()`
and before the decoded `nvkm_error()` line.

Consequently, patch 0011's BAR2 reset BEGIN/END markers execute inside that
existing interrupt recovery path. They add only an atomic sequence increment
and `nvkm_info()` records around the existing `.init()`/`.wait()` calls: no new
MMIO access, allocation, sleeping lock, VMM walk, or mapping-lifetime lookup.
The two printk records can change interrupt timing and are a
**DIAGNOSTIC PERTURBATION**. The normal map/allocation/refcount records remain
in process-context instmem functions. The offline correlator treats any reset
record overlapping a raw-to-decoded fault observation interval as an
inconclusive transition, and only reports a reset as spanning an active map
for a later fault when it completed before that fault's raw record.

## K4200 coverage check for other direct BAR2 mappings

A source search for `NVKM_BAR2_INST`, BAR2 resource-address access, and
`ioremap*()` in the pinned `nvkm/` tree found these other implementations:
`nv40_instmem.c` directly maps the full BAR2 resource for the NV40 instmem
family; GSP R535/R570 code has BAR2 resource mappings; and the user-fault
object maps a pinned fault buffer into userspace. Those are not selected by
GK104/NVE4: `nve4_chipset` selects `nv50_instmem_new`, has no `.fault`
constructor, and has no GSP implementation. The older `nv50_bar_oneinit()`
path accesses BAR2 setup GPU objects through `nvkm_kmap()`, which dispatches
through the selected NV50 instmem object path. Thus patch 0011 instruments the
identified ordinary kernel `ioremap_wc()` BAR2 mapping path for this K4200.
This is source coverage, not proof that every conceivable external/userspace
mapping route has been excluded.

## Reset-to-map reacquisition behavior

`nv50_instobj_acquire()` calls `nv50_instobj_kmap()` only when a BAR2 VMM is
available and `iobj->map` is NULL. If an object retains its cached `map`/`bar`
fields across `nvkm_bar_bar2_reset()`, acquire reuses those fields; the reset
routine itself does not call `nv50_instobj_kmap()` or visit the object list.
This establishes a **SOURCE-PROVEN software-state possibility** relevant to
H3. It does not prove that reset removed a required GPU PTE or translation
cache entry, nor that any current fault used such an object. Patch 0011 logs
map IDs and reset brackets so a later fault can test whether an observed
mapping lifecycle spans a reset and then recurs at the same numeric VA.

## Patch 0011 final-range semantics

The candidate patch is `patches/0011-drm-nouveau-trace-ambient-bar2-lifetimes.patch`
(SHA-256 `42bd387c82d51a39f1167a69dba210857b3c37bf9458311a57dd09705fe12222`).
`LAST_MAP_RELEASE` records the stored `bar` VMA range whether or not the
`ioremap` pointer is present; this snapshot is captured before `memory.ptrs`
is cleared and performs no VMM lookup. The record uses `range=cached` when the
VMA exists. It is deliberately not called a VMA teardown. The parser's recent
range result distinguishes this 1-to-0 kmap reference edge (VMA retained in
LRU) from `EVICTED` or verified `DESTROYED` teardown.
