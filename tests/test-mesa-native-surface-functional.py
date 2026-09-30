#!/usr/bin/env python3
"""Check the Mesa 26.0.8 native-surface VA clear fix without diagnostics."""

import argparse
from pathlib import Path
import sys


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


parser = argparse.ArgumentParser()
parser.add_argument("mesa_source", type=Path,
                    help="Mesa 26.0.8 source after functional candidate patches")
args = parser.parse_args()

root = args.mesa_source / "src/gallium"
paths = {
    "va": root / "frontends/va/surface.c",
    "nvc0_resource": root / "drivers/nouveau/nvc0/nvc0_resource.c",
    "nvc0_miptree": root / "drivers/nouveau/nvc0/nvc0_miptree.c",
    "nv50_resource": root / "drivers/nouveau/nv50/nv50_resource.c",
    "nv50_miptree": root / "drivers/nouveau/nv50/nv50_miptree.c",
}
sources = {name: path.read_text() for name, path in paths.items()}

va = sources["va"]
start = va.find("vlVaHandleSurfaceAllocate(")
end = va.find("\nstruct pipe_video_buffer *\nvlVaGetSurfaceBuffer", start)
if start < 0 or end < 0:
    fail("could not isolate vlVaHandleSurfaceAllocate()")
va_body = va[start:end]

checks = {
    "VA includes pipe surface reference helpers":
        '#include "util/u_inlines.h"' in va,
    "VA creates a driver-native surface from the plane descriptor":
        "drv->pipe->create_surface(drv->pipe," in va_body
        and "surfaces[i].texture," in va_body
        and "&surfaces[i]);" in va_body,
    "VA preserves the generic-surface fallback":
        "clear_surface = &surfaces[i];" in va_body,
    "VA sizes and clears the selected surface object":
        "pipe_surface_size(clear_surface, &width, &height);" in va_body
        and "clear_render_target(drv->pipe, clear_surface," in va_body,
    "VA releases its owned native surface after the clear":
        "pipe_surface_reference(&clear_surface, NULL);" in va_body
        and va_body.index("clear_render_target(drv->pipe, clear_surface,")
        < va_body.index("pipe_surface_reference(&clear_surface, NULL);"),
    "native-surface allocation failure releases the video buffer":
        "surface->buffer->destroy(surface->buffer);" in va_body
        and "surface->buffer = NULL;" in va_body
        and "return VA_STATUS_ERROR_ALLOCATION_FAILED;" in va_body,
    "NVC0 registers native construction and matching destruction":
        "pcontext->create_surface = nvc0_miptree_surface_new;"
        in sources["nvc0_resource"]
        and "pcontext->surface_destroy = nv50_surface_destroy;"
        in sources["nvc0_resource"],
    "NVC0 assigns the owning pipe context":
        "ns->base.context = pipe;" in sources["nvc0_miptree"],
    "NV50 registers native construction and matching destruction":
        "pcontext->create_surface = nv50_miptree_surface_new;"
        in sources["nv50_resource"]
        and "pcontext->surface_destroy = nv50_surface_destroy;"
        in sources["nv50_resource"],
    "NV50 assigns the owning pipe context":
        "ns->base.context = pipe;" in sources["nv50_miptree"],
}

for description, passed in checks.items():
    if not passed:
        fail(description)

if "clear_render_target(drv->pipe, &surfaces[i]" in va_body:
    fail("VA still passes the generic plane array entry directly to clear")

for name, source in sources.items():
    for marker in ("NOUVEAU_DIAG_", "nouveau_vp3_diag", "os_time_get_nano"):
        if marker in source:
            fail(f"diagnostic marker {marker!r} remains in {name}")

print("PASS: VA render-target clears use correctly owned driver surfaces")
print("PASS: no Mesa diagnostic instrumentation remains in the changed sources")
