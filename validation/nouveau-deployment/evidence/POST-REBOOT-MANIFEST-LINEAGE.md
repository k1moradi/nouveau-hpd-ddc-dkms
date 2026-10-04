# Post-reboot admission manifest lineage

This note preserves the deployment-manifest identities that were used around
boot `916996de-2805-4cf1-9dd1-abb2a60380f8`. The boot is contaminated by BAR2 /
HOST_CPU / PTE faults and is not eligible for GPU experiments.

| Artifact | SHA-256 | Admission checker SHA-256 | Deployment plan SHA-256 |
|---|---|---|---|
| Original finalized manifest, retained as `deployment-manifest.pre-sudo-admission-72850afdc253.json` | `72850afdc2534ac3fdcd3c58493b4a4010b412e734d60cfab78ba066892d1e6d` | `12a5dce0364c14232300f5355bfde50c1209f7870a2ead1f586ad78e52356f33` | `3e314f710bb2007ac7dd51ea623735f08164707809c2b1eff22969f007ffa81b` |
| Post-reboot admission revision, currently retained as `deployment-manifest.json` | `d7ad400504cd870acb584ff8108666ce67096d33865a927252fe70b847f42061` | `db124e31730c07f80f1307d0a244ad081839714e2e9b95078912148e9692c69d` | `4be4824ca3b07d4c0b72d0b0f3e73e2cc802df568ef551c241aa4561dbb99a96` |
| Current review-tree admission checker after rejecting `SUDO_UID=0` | `66ba598043da4b79d00993728e4dc8f6c0c2dbdba37e30063963054076e0bf24` | not yet generated | not yet generated |

The active manifest revision followed a post-reboot change that runs the
root-only initramfs inspection through `sudo` while binding admission to the
invoking desktop user's `SUDO_UID`. The follow-up source change rejects
`SUDO_UID=0` so root cannot satisfy the desktop-user gate as its own user. The
retained `d7ad...` manifest pins the immediately preceding checker and is now
historical; it must not be reused for a later deployment. Generate the next
manifest from the reviewed clean tree after this admission change is committed.

Comparing the two retained JSON manifests shows the kernel release, reviewed
Nouveau build/module hashes, srcversion, vermagic, module parameters,
initramfs hash and embedded uncompressed module hash are unchanged. The
changing identities are the admission checker and deployment plan. No kernel
module or initramfs bytes were changed by the admission work.
