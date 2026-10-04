import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "build_nvif_lifetime_ab.py"
SPEC = importlib.util.spec_from_file_location("build_nvif_lifetime_ab", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class BuildInvariantTests(unittest.TestCase):
    def test_exported_symbol_interface_ignores_link_address_but_keeps_shape(self) -> None:
        a = "__vaDriverInit_1_23 T eb210 352\n"
        b = "__vaDriverInit_1_23 T eb230 352\n"
        changed_size = "__vaDriverInit_1_23 T eb230 360\n"

        self.assertEqual(
            BUILD.parse_exported_dynamic_symbols(a)[0],
            BUILD.parse_exported_dynamic_symbols(b)[0],
        )
        self.assertNotEqual(
            BUILD.parse_exported_dynamic_symbols(a)[0],
            BUILD.parse_exported_dynamic_symbols(changed_size)[0],
        )

    def test_patch_pin_rejects_changed_patch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvif-patch-pin-") as temporary:
            patch = Path(temporary) / "diagnostic.patch"
            patch.write_text("different patch\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                BUILD.require_sha256(
                    patch,
                    BUILD.EXPECTED_DIAGNOSTIC_PATCH_SHA256,
                    "pinned diagnostic patch",
                )

    def test_base_metadata_hashes_are_checked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvif-base-metadata-") as temporary:
            root = Path(temporary)
            expected = {}
            for relative in BUILD.EXPECTED_BASE_BUILD_METADATA_SHA256:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("ascii"))
                expected[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

            original = BUILD.EXPECTED_BASE_BUILD_METADATA_SHA256
            try:
                BUILD.EXPECTED_BASE_BUILD_METADATA_SHA256 = expected
                self.assertEqual(BUILD.verify_base_metadata(root), expected)
                clone = root / "clone"
                for relative in expected:
                    if relative == "build.ninja":
                        continue
                    path = clone / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((root / relative).read_bytes())
                self.assertEqual(
                    BUILD.verify_cloned_metadata(clone),
                    {
                        key: value
                        for key, value in expected.items()
                        if key != "build.ninja"
                    },
                )
                (root / "build.ninja").write_bytes(b"mutated")
                with self.assertRaisesRegex(RuntimeError, "unexpected"):
                    BUILD.verify_base_metadata(root)
            finally:
                BUILD.EXPECTED_BASE_BUILD_METADATA_SHA256 = original

    def test_read_only_version_script_is_pinned(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nvif-version-script-") as temporary:
            root = Path(temporary)
            version_script = root / "src/gallium/targets/va/va.sym"
            version_script.parent.mkdir(parents=True)
            version_script.write_text("VERS_1 { global: va*; };\n", encoding="utf-8")
            expected = hashlib.sha256(version_script.read_bytes()).hexdigest()
            original = BUILD.EXPECTED_BASE_VERSION_SCRIPT_SHA256
            try:
                BUILD.EXPECTED_BASE_VERSION_SCRIPT_SHA256 = expected
                self.assertEqual(BUILD.verify_base_version_script(root), expected)
                version_script.write_text("changed\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "unexpected"):
                    BUILD.verify_base_version_script(root)
            finally:
                BUILD.EXPECTED_BASE_VERSION_SCRIPT_SHA256 = original

    def test_ninja_pair_allows_exactly_one_candidate_define(self) -> None:
        a = b"ARGS = -O2 -DNOUVEAU_DIAG_NVIF_LIFETIME\n"
        b = a + (
            "ARGS = -O2 -DNOUVEAU_DIAG_NVIF_LIFETIME "
            "-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD\n"
        ).encode("ascii")

        with self.assertRaisesRegex(RuntimeError, "differ beyond"):
            BUILD.verify_ab_ninja(a, b)

        expected_a = (
            "build edge\n"
            "  ARGS = -O2 -DNOUVEAU_DIAG_NVIF_LIFETIME\n"
        ).encode("ascii")
        expected_b = (
            "build edge\n"
            "  ARGS = -O2 -DNOUVEAU_DIAG_NVIF_LIFETIME "
            "-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD\n"
        ).encode("ascii")
        BUILD.verify_ab_ninja(expected_a, expected_b)
        duplicated = expected_b.replace(
            b"-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD\n",
            b"-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD "
            b"-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD\n",
        )
        with self.assertRaisesRegex(RuntimeError, "one candidate define"):
            BUILD.verify_ab_ninja(expected_a, duplicated)

    def test_compile_commands_require_one_candidate_flag(self) -> None:
        a = "cc -O2 -DNOUVEAU_DIAG_NVIF_LIFETIME -c nouveau.c"
        b = a.replace("-c nouveau.c", "-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD -c nouveau.c")
        BUILD.verify_compile_commands(a, b)

        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            BUILD.verify_compile_commands(a, b + " -DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD")

    def test_target_graph_accepts_only_one_define_and_read_only_base_script(
        self,
    ) -> None:
        base = Path("/tmp/base-build")
        version_script = base / "src/gallium/targets/va/va.sym"
        a = [
            "cc -DNOUVEAU_DIAG_NVIF_LIFETIME -c nouveau.c",
            f"c++ -o target.so -Wl,--version-script {version_script}",
        ]
        b = [
            a[0] + " -DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD",
            a[1],
        ]

        self.assertEqual(BUILD.verify_target_commands(a, b, base), 0)

    def test_target_graph_rejects_unrelated_command_change(self) -> None:
        base = Path("/tmp/base-build")
        version_script = base / "src/gallium/targets/va/va.sym"
        a = [
            "cc -DNOUVEAU_DIAG_NVIF_LIFETIME -c nouveau.c",
            f"c++ -o target.so -Wl,--version-script {version_script}",
        ]
        b = [
            "cc -DNOUVEAU_DIAG_NVIF_LIFETIME -c nouveau.c "
            "-DNOUVEAU_DIAG_NVIF_CORRECT_DEL_FD",
            f"c++ -o target.so -Wl,--as-needed -Wl,--version-script {version_script}",
        ]

        with self.assertRaisesRegex(RuntimeError, "command graphs"):
            BUILD.verify_target_commands(a, b, base)

    def test_baseline_tree_output_reference_is_rejected(self) -> None:
        base = Path("/tmp/base-build")
        command = f"cc -o {base}/target.so input.o"

        with self.assertRaisesRegex(RuntimeError, "unexpected baseline build path"):
            BUILD.verify_base_build_references([command], base)

    def test_baseline_version_script_must_be_read_only_input(self) -> None:
        base = Path("/tmp/base-build")
        version_script = base / "src/gallium/targets/va/va.sym"
        command = f"c++ -o target.so -Wl,--version-script {version_script}"
        self.assertEqual(
            BUILD.verify_base_build_references([command], base),
            1,
        )

        with self.assertRaisesRegex(RuntimeError, "read-only version-script"):
            BUILD.verify_base_build_references(
                [f"cp {version_script} /tmp/copy"],
                base,
            )


if __name__ == "__main__":
    unittest.main()
