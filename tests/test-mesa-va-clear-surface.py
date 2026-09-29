#!/usr/bin/env python3
"""Check the Mesa VA clear candidate preserves Nouveau surface metadata."""

import argparse
from pathlib import Path
import sys


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


parser = argparse.ArgumentParser()
parser.add_argument("mesa_source", type=Path,
                    help="Mesa 26.0.8 source after diagnostic and candidate patches")
args = parser.parse_args()

va_path = args.mesa_source / "src/gallium/frontends/va/surface.c"
nvc0_resource_path = (args.mesa_source /
                      "src/gallium/drivers/nouveau/nvc0/nvc0_resource.c")
nv50_resource_path = (args.mesa_source /
                      "src/gallium/drivers/nouveau/nv50/nv50_resource.c")
nvc0_miptree_path = (args.mesa_source /
                     "src/gallium/drivers/nouveau/nvc0/nvc0_miptree.c")
nv50_miptree_path = (args.mesa_source /
                     "src/gallium/drivers/nouveau/nv50/nv50_miptree.c")

va_source = va_path.read_text()
nvc0_resource = nvc0_resource_path.read_text()
nv50_resource = nv50_resource_path.read_text()
nvc0_miptree = nvc0_miptree_path.read_text()
nv50_miptree = nv50_miptree_path.read_text()

start = va_source.find("vlVaHandleSurfaceAllocate(")
end = va_source.find("\nstruct pipe_video_buffer *\nvlVaGetSurfaceBuffer", start)
if start < 0 or end < 0:
    fail("could not isolate vlVaHandleSurfaceAllocate()")
va_body = va_source[start:end]

constructor_start = nvc0_miptree.find("nvc0_miptree_surface_new(")
constructor_end = nvc0_miptree.find("\n}", constructor_start)
if constructor_start < 0 or constructor_end < 0:
    fail("could not isolate nvc0_miptree_surface_new()")
constructor_body = nvc0_miptree[constructor_start:constructor_end]

nv50_constructor_start = nv50_miptree.find("nv50_miptree_surface_new(")
nv50_constructor_end = nv50_miptree.find("\n}", nv50_constructor_start)
if nv50_constructor_start < 0 or nv50_constructor_end < 0:
    fail("could not isolate nv50_miptree_surface_new()")
nv50_constructor_body = nv50_miptree[nv50_constructor_start:nv50_constructor_end]

derived_start = nv50_miptree.find("nv50_surface_from_miptree(")
derived_end = nv50_miptree.find("\n}", derived_start)
if derived_start < 0 or derived_end < 0:
    fail("could not isolate nv50_surface_from_miptree()")
derived_body = nv50_miptree[derived_start:derived_end]

checks = {
    "VA clear path includes the surface reference helpers":
        '#include "util/u_inlines.h"' in va_source,
    "VA creates a native surface from the generic plane descriptor":
        "drv->pipe->create_surface(drv->pipe," in va_body
        and "surfaces[i].texture," in va_body
        and "&surfaces[i]);" in va_body,
    "VA clears through the created surface":
        "clear_render_target(drv->pipe, clear_surface," in va_body,
    "VA releases an owned surface after the callback":
        "pipe_surface_reference(&clear_surface, NULL);" in va_body
        and va_body.index("clear_render_target(drv->pipe, clear_surface,")
        < va_body.index("pipe_surface_reference(&clear_surface, NULL);"),
    "VA preserves legacy generic-surface fallback when no callback exists":
        "clear_surface = &surfaces[i];" in va_body,
    "surface creation failure destroys and clears the video buffer":
        "surface->buffer->destroy(surface->buffer);" in va_body
        and "surface->buffer = NULL;" in va_body
        and "return VA_STATUS_ERROR_ALLOCATION_FAILED;" in va_body,
    "NVC0 registers its native constructor and matching destructor":
        "pcontext->create_surface = nvc0_miptree_surface_new;" in nvc0_resource
        and "pcontext->surface_destroy = nv50_surface_destroy;" in nvc0_resource,
    "NVC0 native constructor assigns the surface owner":
        "ns->base.context = pipe;" in constructor_body,
    "NV50 registers its native constructor and existing destructor":
        "pcontext->create_surface = nv50_miptree_surface_new;" in nv50_resource
        and "pcontext->surface_destroy = nv50_surface_destroy;" in nv50_resource,
    "NV50 native constructor assigns the surface owner":
        "ns->base.context = pipe;" in nv50_constructor_body,
    "native NVC0 constructor builds from the supplied descriptor":
        "nv50_surface_from_miptree(nv50_miptree(pt), templ)" in constructor_body,
    "native surface retains format, level, and layer range":
        "ps->format = templ->format;" in derived_body
        and "ps->level = templ->level;" in derived_body
        and "ps->first_layer = templ->first_layer;" in derived_body
        and "ps->last_layer = templ->last_layer;" in derived_body,
    "native surface derives private extent, depth, and offset":
        "ns->width = u_minify" in derived_body
        and "ns->height = u_minify" in derived_body
        and "ns->depth = ps->last_layer - ps->first_layer + 1;" in derived_body
        and "ns->offset = mt->level[templ->level].offset;" in derived_body,
}

for description, passed in checks.items():
    if not passed:
        fail(description)

if "clear_render_target(drv->pipe, &surfaces[i]" in va_body:
    fail("VA still directly passes the generic array descriptor to NVC0")

print("PASS: VA clear uses a context-owned native Nouveau surface with the "
      "descriptor's format, level, and layer range")
