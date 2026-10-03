import hashlib
import os
from pathlib import Path
import unittest


SOURCE_ROOT = Path(os.environ.get("NOUVEAU_KERNEL_SOURCE_ROOT", ""))
ABI16_PATH = SOURCE_ROOT / "drivers/gpu/drm/nouveau/nouveau_abi16.c"
NVKM_IOCTL_PATH = SOURCE_ROOT / "drivers/gpu/drm/nouveau/nvkm/core/ioctl.c"
NVKM_OBJECT_PATH = SOURCE_ROOT / "drivers/gpu/drm/nouveau/nvkm/core/object.c"
ABI16_SHA256 = "3d4c81dd87c8e426b7e948eb448a49075d1df54ab22c68b8e181a0b88bd28fec"
NVKM_IOCTL_SHA256 = "2a18013840cee2298edadd19171b083cfed2bb7227a13303ac9afad34a107e18"
NVKM_OBJECT_SHA256 = "7dc1fca97b143da107a1234d467774608b0808ca84f88f368313941126890c6d"


def function_body(source: str, name: str) -> str:
    marker = name + "("
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    index = opening
    state = "code"

    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and following == "/":
                state = "code"
                index += 1
        elif state in {"string", "char"}:
            terminator = '"' if state == "string" else "'"
            if char == "\\":
                index += 1
            elif char == terminator:
                state = "code"
        elif char == "/" and following == "/":
            state = "line_comment"
            index += 1
        elif char == "/" and following == "*":
            state = "block_comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening:index + 1]
        index += 1

    raise AssertionError(f"unterminated function body: {name}")


@unittest.skipUnless(
    ABI16_PATH.is_file()
    and NVKM_IOCTL_PATH.is_file()
    and NVKM_OBJECT_PATH.is_file(),
    "set NOUVEAU_KERNEL_SOURCE_ROOT to the pinned v1-v8 kernel source",
)
class Abi16DuplicateKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.abi16_source = ABI16_PATH.read_text(encoding="utf-8")
        cls.nvkm_ioctl_source = NVKM_IOCTL_PATH.read_text(encoding="utf-8")
        cls.nvkm_object_source = NVKM_OBJECT_PATH.read_text(encoding="utf-8")

    def test_pinned_abi16_source_identity(self) -> None:
        self.assertEqual(
            hashlib.sha256(ABI16_PATH.read_bytes()).hexdigest(),
            ABI16_SHA256,
        )

    def test_pinned_nvkm_ioctl_source_identity(self) -> None:
        self.assertEqual(
            hashlib.sha256(NVKM_IOCTL_PATH.read_bytes()).hexdigest(),
            NVKM_IOCTL_SHA256,
        )

    def test_pinned_nvkm_object_source_identity(self) -> None:
        self.assertEqual(
            hashlib.sha256(NVKM_OBJECT_PATH.read_bytes()).hexdigest(),
            NVKM_OBJECT_SHA256,
        )

    def test_abi16_rejects_existing_object_key_before_ctor(self) -> None:
        obj_new = function_body(self.abi16_source, "nouveau_abi16_obj_new")
        find_key = obj_new.index("nouveau_abi16_obj_find(abi16, object)")
        reject = obj_new.index("return ERR_PTR(-EEXIST);")
        allocate = obj_new.index("kzalloc_obj(*obj)")
        self.assertLess(find_key, reject)
        self.assertLess(reject, allocate)

        ioctl_new = function_body(
            self.abi16_source,
            "nouveau_abi16_ioctl_new",
        )
        insert = ioctl_new.index(
            "nouveau_abi16_obj_new(abi16, ENGOBJ, args->object)"
        )
        ctor = ioctl_new.index("nvif_object_ctor(")
        self.assertLess(insert, ctor)
        self.assertIn("return PTR_ERR(obj);", ioctl_new[insert:ctor])

    def test_channel_fini_does_not_remove_client_object_key(self) -> None:
        channel_fini = function_body(
            self.abi16_source,
            "nouveau_abi16_chan_fini",
        )
        self.assertIn("nouveau_channel_del(&chan->chan);", channel_fini)
        self.assertNotIn("nouveau_abi16_obj_del(", channel_fini)

        client_fini = function_body(
            self.abi16_source,
            "nouveau_abi16_fini",
        )
        self.assertIn("&abi16->objects", client_fini)
        self.assertIn("nouveau_abi16_obj_del(obj);", client_fini)

        ioctl_del = function_body(
            self.abi16_source,
            "nouveau_abi16_ioctl_del",
        )
        find_key = ioctl_del.index("nouveau_abi16_obj_find(abi16, ioctl->object)")
        remove_key = ioctl_del.index("nouveau_abi16_obj_del(obj)")
        self.assertLess(find_key, remove_key)

    def test_nvkm_has_a_separate_duplicate_object_return(self) -> None:
        nvkm_new = function_body(self.nvkm_ioctl_source, "nvkm_ioctl_new")
        insert = nvkm_new.index("nvkm_object_insert(object)")
        duplicate = nvkm_new.index("ret = -EEXIST;", insert)
        self.assertLess(insert, duplicate)

        object_insert = function_body(
            self.nvkm_object_source,
            "nvkm_object_insert",
        )
        self.assertIn("object->object < this->object", object_insert)
        self.assertIn("object->object > this->object", object_insert)
        self.assertIn("return false;", object_insert)


if __name__ == "__main__":
    unittest.main()
