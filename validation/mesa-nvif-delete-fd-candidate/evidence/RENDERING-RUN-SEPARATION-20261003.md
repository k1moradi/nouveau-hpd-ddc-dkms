# Corrected DEL-fd candidate: separate playback and screenshot evidence

## Scope

This note reconciles the retained files in
`/home/keivan/nouveau-vaapi-app-validation/mpv-candidate-short-20261001T142951Z/`.
It is a read-only review of historical artifacts. No player, VA/GPU workload,
module action, install, or reboot was performed.

## The saved mpv log and screenshots are from different runs

The retained `mpv.log` has SHA-256
`b33ce9bfa6e123ccc0486d9cf9fce8ad4e4b504f36323acfb8a5d4ee8a72bb82` and
mtime `2026-10-01 07:30:31 -0700`. Its command line requests
`--hwdec=vaapi-copy`, `--vo=gpu`, and `--length=30`. The log records:

- the private candidate VA DSO opened successfully;
- `Using hardware decoding (vaapi-copy)`;
- playback status advancing from 0 to 29 seconds;
- `video EOF reached` at the configured 30-second limit and process exit 0.

This shows that this run selected the candidate VAAPI path and advanced its
playback clock. The user reported a diagonal triangular region with blank or
corrupt video during this first run, but no screenshot was captured during it.
The exit status and clock advancement do not establish correct presentation.

The retained screenshot files have mtimes around `07:38:19` to `07:38:48`,
roughly eight minutes after the saved log and its exit record. The prior
`RESULTS.md` identifies these as the later, approximately 20-second replay.
`mpv-visible-corruption.png` (SHA-256
`3321037397e77bacf37bf85ade10595f6c712167def27155d6c5f3871866cc71`) shows a
black video pane. The four `mpv-live-*.png` desktop captures are also black.
These screenshots must not be paired with the earlier 30-second log. The
later replay stopped before the bright title-card interval, so its black
frame alone does not identify a decode or presentation defect.

## Causal limits

The run log does not include the NVIF lifetime diagnostic records. It provides
no old/new BSP object key, DEL fd, DEL result, or kernel duplicate layer. It
therefore does not prove that corrected DEL removed a stale ABI16 key or that
the change caused the later playback-clock progress. The separate v5
constructor probe and the candidate playback run remain distinct evidence.

The CPU-side FFmpeg NV12 comparison for this candidate found the selected early
frames close to the software reference, but those samples do not explain the
user-reported triangular rendering. No synchronized hardware NV12 capture was
retained for the affected visible frames.

## Conclusion

```text
VAAPI-COPY ACTIVE IN SAVED 30-SECOND RUN: YES
PLAYBACK CLOCK ADVANCED IN THAT RUN: YES
CORRECT VISIBLE VIDEO: NO (user reported corruption; no first-run screenshot)
BLACK LATER REPLAY SCREENSHOTS: YES (separate, short replay)
CORRECTED DEL-FD CAUSALITY: NOT PROVEN
PRODUCTION FIX: NOT ACCEPTED
```

Any future causal A/B must capture the VA/NVIF lifecycle in the same run as
the player result and use screenshot timestamps tied to the player media time.
Hardware execution still requires the reviewed kernel deployment and an
eligible local desktop boot; this note does not authorize or perform that run.
