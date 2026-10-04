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
  transitions. In correlator output, a match after `KMAP_LAST_RELEASE` means
  only that the CPU map-reference count reached zero; the VMA remains resident
  in the LRU. It does not claim teardown. Only a later `VMA_EVICTED` or an
  `OBJECT_DESTROYED` record with `vma_state=destroyed` records source-observed
  VMA teardown.
- LRU eviction: the next `nv50_instobj_kmap()` can remove the first unused
  object from the LRU, `iounmap()` its mapping, and call `nvkm_vmm_put()` to
  remove the VMA/PTE mapping.
- Object destruction: `nv50_instobj_dtor()` removes it from LRU, captures
  `map`/`bar`, `iounmap()`s and `nvkm_vmm_put()`s the VMA when BAR2 VMM exists,
  unreferences backing RAM, and removes the instobj from the instmem list.

The existing code does not assert in the destructor that `maps == 0`. Patch
0011 records `refs` at `OBJECT_DESTROYING` and lets the offline parser mark a
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
| H1 active-map PTE failure | Fault VA is inside one resident VMA whose ID has `kmap_ref_state_at_fault=NONZERO`; `exact_kmap_refcount_at_fault` remains unknown because nested refs do not emit extra events. | Unresolved; current boot has no ambient lifetime records. |
| H2 post-eviction access | Fault VA matches an ID's range after `VMA_EVICTED` or `OBJECT_DESTROYED` with `vma_state=destroyed`; report its eviction/destroy-to-fault delta and any active replacement range separately. `KMAP_LAST_RELEASE` is not eviction. | Unresolved. Numeric reuse is not ownership or stale-pointer proof. |
| H3 reset/cache coherence | A mapping has `map_reset_gen < current_reset_gen`; a later `KMAP_ACTIVE map_source=cached` on the same ID/range records cache reuse after reset. Compare this with later fault ordinals; never use recovery after fault 1 to explain fault 1. | Unresolved; C source shows software state can survive reset, but not the hardware translation effect. |
| H4 initial establishment | Fault time intersects `MAP_BEGIN`, `MAP_VMA_RESERVED`, `MAP_VMM_MAP_OK`, or `MAP_READY` before `KMAP_ACTIVE`; report when the VA first becomes known. `MAP_VMM_MAP_OK` means the mapping API returned success, not hardware PTE readback. | Unresolved. |
| H5 untracked BAR2 use | No active, zero-ref resident, transition, or recently evicted/destroyed range in the observed records numerically matches the fault. | Unresolved. Input completeness is not proven, so no match does not prove global absence or identify another user. |

Patch 0011 emits the lifecycle and reset events listed below. The reserve/map
edges expose setup windows because page-table allocation can recurse; the
eviction and destroy edges distinguish true software VMA removal from a final
`nvkm_done()` that only leaves a zero-ref VMA cached in the LRU.

## Patch 0011 event contract

`pid` and `comm` identify the task executing a process-context transition.
They do not identify the owner of a later hardware fault. `mem_addr` is backing
memory context only; the correlator matches faults only against `bar2_va` and
`bar2_len`. `KMAP_ACTIVE refs=1` records the 0-to-1 edge. Because nested kmap
increments are intentionally not logged, an active candidate proves a
nonzero-reference state, not the exact nested refcount at the fault.

| Event | Source function | Lock/context | State observed / diagnostic state changed | Additional MMIO | Added allocation or sleep risk | Address domain |
|---|---|---|---|---|---|---|
| `ALLOCATED` | `nv50_instobj_wrap()` | Process context; after instobj construction and backing-memory ref | Assigns a boot-local atomic ID once; starts with no VMA and zero refs | No | No diagnostic allocation or sleep; existing object allocation is unchanged | `mem_addr`/`mem_len` context; no BAR2 range yet |
| `MAP_BEGIN` | `nv50_instobj_kmap()` | Process context; instmem mutex held before the existing unlock | Starts a map attempt; no VMA range known; attempt ID assigned atomically | No | No added allocation or sleep | No BAR2 range yet |
| `MAP_VMA_RESERVED` | `nv50_instobj_kmap()` after `nvkm_vmm_get()` | Process context; instmem mutex is dropped; VMM allocator uses its existing locking | Temporary VMA reserved; establishment in progress | No diagnostic MMIO; the existing VMM call is unchanged | No added allocation or sleep beyond existing mapping path | `bar2_va`/`bar2_len` |
| `MAP_VMM_MAP_OK` | `nv50_instobj_kmap()` immediately after successful `nvkm_memory_map()` | Process context; instmem mutex is dropped | VMM map API returned success; if diagnostics are enabled, snapshots the reset generation here; this is not hardware PTE readback | No diagnostic MMIO | No added allocation or sleep beyond existing map path | Reserved BAR2 VMA range |
| `MAP_READY` | `nv50_instobj_kmap()` after successful `ioremap_wc()` | Process context; instmem mutex held | Stores `iobj->bar` and `iobj->map` and carries forward the generation captured at `MAP_VMM_MAP_OK`; later `current_reset_gen` exposes resets during ioremap | No diagnostic MMIO | `ioremap_wc()` is pre-existing; no added diagnostic allocation or sleep | Resident BAR2 VMA range |
| `MAP_FAILED`, `MAP_DISCARDED`, rollback begin/done | `nv50_instobj_kmap()` | Process context; logging occurs at the existing lock/unlock edges | Reports failed or losing map attempt and whether its reserved VMA is rolled back | No diagnostic MMIO | No added allocation or sleep | Attempt BAR2 range when already reserved |
| `KMAP_ACTIVE` | `nv50_instobj_acquire()` | Process context; instmem mutex held after accessors and refcount are published | Records 0-to-1 CPU map-ref edge, access mode, and `map_source=new|cached|none` | No | No added allocation or sleep | Current resident BAR2 VMA, if BAR2 access |
| `KMAP_LAST_RELEASE` | `nv50_instobj_release()` | Process context; after refcount reaches zero with instmem mutex held | Records 1-to-0 CPU ref edge before clearing `memory.ptrs`; the VMA remains resident and may be `cache_state=lru` or `pinned` | No diagnostic MMIO; the existing BAR flush occurs before this log | No added allocation or sleep | Stored BAR2 VMA snapshot; no post-release lookup |
| `BOOT_MAP_PINNED` | `nv50_instobj_boot()` | Process context; instmem mutex held after map helper returns | Records a resident VMA excluded from LRU; zero active kmap refs | No | No added allocation or sleep | Stored BAR2 VMA range |
| `VMA_EVICTING` | `nv50_instobj_kmap()` LRU eviction branch | Process context; instmem mutex held before LRU removal and pointer clear | Snapshots ID/range/reset generation before detaching the cached mapping | No | No added allocation or sleep | Stored BAR2 VMA range |
| `VMA_EVICTED` | `nv50_instobj_kmap()` after `iounmap()` and `nvkm_vmm_put()` | Process context; instmem mutex dropped; VMM put uses its existing sleeping lock | Records successful software VMA removal after the VMM unmap/put path | No diagnostic MMIO | No added allocation or sleep beyond existing teardown | Previously stored BAR2 VMA range |
| `OBJECT_DESTROYING` | `nv50_instobj_dtor()` | Process context; instmem mutex held while final state is snapshotted | Captures refs, resident/cached state and final range before LRU removal/destruction; nonzero refs remain visible | No | No added allocation or sleep | Stored BAR2 VMA range |
| `OBJECT_DESTROYED` | `nv50_instobj_dtor()` after iounmap/VMM put/backing unref/list removal | Process context; no instmem mutex held at log site | Reports `destroyed` only when a resident VMA was actually passed to `nvkm_vmm_put()`; otherwise `unknown` or `not_established` | No diagnostic MMIO | No added allocation or sleep beyond existing destruction | Snapshotted BAR2 VMA range |
| reset `BEGIN` / `END` | `nvkm_bar_bar2_reset()` | Existing FIFO recovery interrupt context | Atomically increments reset generation at `BEGIN`; brackets the existing BAR2 `.init()`/`.wait()` operation | No additional MMIO reads; existing reset writes/flushes are unchanged | No explicit allocation or sleeping lock; two printk records are a diagnostic perturbation | Reset generation only |

The reset-generation increment occurs immediately before the existing reset
`.init()`/`.wait()` calls. It records reset attempts and ordering; it does not
claim that a particular internal GPU TLB/PTE cache was invalidated. The global
diagnostic sequence can reveal dropped/reordered log records; the correlator
uses journal monotonic timestamp and line order for lifecycle state and treats
sequence numbers as completeness checks, not as a substitute for time.

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
(SHA-256 `67f0ba18f3e4ce979bc97784f622a33e0eb5f6e0472b7de4cd3d96925004e625`).
`KMAP_LAST_RELEASE` records the stored `bar` VMA range whether or not the
`ioremap` pointer is present; this snapshot is captured before `memory.ptrs`
is cleared and performs no VMM lookup. A resident record uses
`vma_state=resident` and `cache_state=lru|pinned`. It is deliberately not
called a VMA teardown. The parser distinguishes this 1-to-0 kmap-reference
edge (VMA retained in the LRU) from `VMA_EVICTED` or an `OBJECT_DESTROYED`
record that confirms teardown.
