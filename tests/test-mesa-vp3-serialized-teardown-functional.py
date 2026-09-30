#!/usr/bin/env python3
"""Check VP3 functional teardown ordering without diagnostic instrumentation."""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} <Mesa source tree>")

    source = (Path(sys.argv[1]) /
              "src/gallium/drivers/nouveau/nouveau_vp3_video.c")
    text = source.read_text()
    start = text.index(
        "nouveau_vp3_decoder_destroy(struct pipe_video_codec *decoder)")
    end = text.index("\nvoid\nnouveau_vp3_decoder_init_common", start)
    body = text[start:end]
    split = body.index("if (dec->channel[0] != dec->channel[1])")
    shared_start = body.index("} else {", split)
    separate = body[split:shared_start]
    shared = body[shared_start:]

    assert "i == 0 ? &dec->bsp" in separate
    assert "i == 1 ? &dec->vp : &dec->ppp" in separate
    assert separate.index("nouveau_object_del(engine)") < separate.index(
        "nouveau_pushbuf_destroy(&dec->pushbuf[i])"
    ) < separate.index("nouveau_object_del(&dec->channel[i])")

    for engine in ("bsp", "vp", "ppp"):
        assert f"nouveau_object_del(&dec->{engine})" in shared
    assert shared.index("nouveau_object_del(&dec->bsp)") < shared.index(
        "nouveau_object_del(&dec->vp)"
    ) < shared.index("nouveau_object_del(&dec->ppp)")
    assert shared.index("nouveau_object_del(&dec->ppp)") < shared.index(
        "nouveau_pushbuf_destroy(dec->pushbuf)"
    ) < shared.index("nouveau_object_del(dec->channel)")

    for marker in ("NOUVEAU_DIAG_", "nouveau_vp3_diag", "os_time_get_nano"):
        assert marker not in body, f"diagnostic marker remains: {marker}"

    print("PASS: distinct VP3 channels are destroyed engine/pushbuf/channel")
    print("PASS: shared-channel teardown keeps the original lifetime order")
    print("PASS: no VP3 diagnostic instrumentation remains in the function")


if __name__ == "__main__":
    main()
