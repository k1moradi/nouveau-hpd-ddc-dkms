# Known issues

## Intermittent GK104 BAR2 PTE fault

A diagnostic-free full-file H.264 VA-API run on the Quadro K4200 completed
40,561 output and decoded frames with zero decode errors under production
Nouveau `0.1.13` and the private functional Mesa 26.0.8 candidate. Near
FFmpeg process exit, Nouveau also logged a READ PTE fault through BAR2 from
`HUB/HOST_CPU` at BAR2 offset `0x3a4000`, on channel `-1`/unknown. The capture
harness set `STOP_A_B=1` for that kernel event even though FFmpeg exited 0.

No PROP overrun, video-engine `CTXSW_TIMEOUT`, channel kill, failed idle,
privilege violation, SIGBUS, or GPU reset appeared in that capture. The
faulting mapping and any relationship to VA-API teardown are unknown. Earlier
diagnostic captures traced map lifetimes at `0x377000` and `0x388000`; their
filter did not include the later `0x3a4000` address. Do not describe BAR2 as
fixed or attribute the fault to Mesa without new evidence.

The documented full-file result establishes successful decoding of the tested
H.264 file on this hardware and software combination. It does not establish
that every Nouveau BAR2 fault is resolved or that the fault cannot recur.

## Mesa patch scope

The native-surface clear change has direct K4200 runtime evidence. The same
constructor contract is added for NV50, but that path was not independently
hardware-tested. The VP3 teardown change is exercised on the K4200's separate
BSP/VP/PPP-channel path; the shared-channel path remains unchanged.

The Mesa patches are source inputs for a separate Mesa build. The Nouveau
DKMS installer does not install Mesa or apply those patches to system Mesa.
