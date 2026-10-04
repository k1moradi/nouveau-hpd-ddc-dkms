# Late-scene screenshot and software-reference check

## Inputs

- User-reported screenshot:
  /home/keivan/nouveau-vaapi-app-validation/mpv-full-visible-20261001T155451Z/desktop-user-reported-pixelation.png
  SHA-256 6a08474969bb974c8868fa2a3adda6676799af964b4362dd248509c173e5e270.
- Pinned input: /home/keivan/test_1080p.mkv, SHA-256
  d1bab5275bcb585791fbfb15c801c1aab582256e7b7fca280c76f78a0a1c1ec2.
- Software-only NV12 capture:
  /home/keivan/nouveau-vaapi-app-validation/late-pixelation-sw-530-20261004/software.nut,
  SHA-256 8c53f7ea946dbf36d9588aae95051e9f2b9bfbd92dfe83806ace8744bb029aa1.
- Capture manifest SHA-256:
  b05d433fde7d37581464cafe356c68de6eab20a672ffa9c36b91758eb54b291d.
- FFmpeg: /usr/bin/ffmpeg, version 8.0.1-3ubuntu2, SHA-256
  bdf6aabffdba7411edff8d36c389d695257fcdf823d196020176e117612862f6.

## Observations

The screenshot was visually inspected at original resolution. The displayed
scene is coherent and has no obvious square macroblock grid in this still.
That does not contradict the user's report of transient blockiness in other
frames.

The software capture contains five 1920x1080 NV12 frames with recorded PTS
530.021, 530.063, 530.105, 530.146, and 530.188 seconds. These are
CPU/software-decoded references only. The screenshot has no decoded-frame PTS,
so its comparison to this window is approximate.

For a fixed active-picture crop, the screenshot was compared against each
reference after conversion to luma and downsampling to 480x204. The best of
the five sampled references was PTS 530.063:

| Metric | Value |
|---|---:|
| Luma MAE | 4.563 / 255 |
| Luma RMSE | 6.963 / 255 |
| Correlation | 0.994778 |
| Global SSIM-style score | 0.994074 |

The next-best sampled frame was PTS 530.146, with effectively the same
score. The five frames are only 42 ms apart, while the screenshot timing is
not frame-locked; the near-tie is expected and must not be interpreted as an
exact frame match. The global score can also dilute a small localized error.

## Boundary

No hardware NV12 frame exists for this scene. Therefore this check does not
establish whether corruption was already present in the decoded hardware
surface or introduced in presentation. Pixelation remains unresolved. The
next decisive comparison is repeated, timestamped hardware NV12 at these
same PTS values against this retained software reference, after kernel and
deployment admission.

No VA/GPU workload was run for this evidence. The FFmpeg command used
format=nv12 without -hwaccel; the probe's hardware mode remains unexecuted.
