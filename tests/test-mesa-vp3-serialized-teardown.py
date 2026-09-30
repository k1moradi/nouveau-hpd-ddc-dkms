#!/usr/bin/env python3
"""Check the Mesa-only VP3 teardown-order candidate in a source tree."""

from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} <Mesa source tree>")

    source = Path(sys.argv[1]) / "src/gallium/drivers/nouveau/nouveau_vp3_video.c"
    text = source.read_text()
    start = text.index("nouveau_vp3_decoder_destroy(struct pipe_video_codec *decoder)")
    end = text.index("\nvoid\nnouveau_vp3_decoder_init_common", start)
    body = text[start:end]
    branch_start = body.index("if (dec->channel[0] != dec->channel[1])")
    shared_start = body.index("} else {", branch_start)
    separate = body[branch_start:shared_start]
    shared = body[shared_start:]

    assert "i == 0 ? &dec->bsp" in separate
    assert "i == 1 ? &dec->vp : &dec->ppp" in separate
    assert separate.index("nouveau_object_del(engine)") < separate.index(
        "nouveau_pushbuf_destroy(&dec->pushbuf[i])"
    ) < separate.index("nouveau_object_del(&dec->channel[i])")

    for engine in ("bsp", "vp", "ppp"):
        assert f"phase=object-destroy stage=begin engine={engine}" in shared
        assert f"nouveau_object_del(&dec->{engine})" in shared
    assert shared.index("nouveau_object_del(&dec->bsp)") < shared.index(
        "nouveau_object_del(&dec->vp)"
    ) < shared.index("nouveau_object_del(&dec->ppp)")
    assert shared.index("nouveau_object_del(&dec->ppp)") < shared.index(
        "nouveau_pushbuf_destroy(dec->pushbuf)"
    ) < shared.index("nouveau_object_del(dec->channel)")

    for marker in (
        "phase=channel-identity",
        "phase=object-destroy stage=begin engine=%s",
        "phase=channel-destroy stage=object-begin",
    ):
        assert marker in body, f"missing expected diagnostic marker: {marker}"

    print("PASS: separate VP3 channels are destroyed engine/pushbuf/channel in order")
    print("PASS: shared-channel teardown retains all-engine-objects-first order")


if __name__ == "__main__":
    main()
