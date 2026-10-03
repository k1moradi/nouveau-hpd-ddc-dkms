from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "candidate/nouveau_vp3_video_vp.c").read_text()


def function_body(name: str) -> str:
    marker = name + "("
    start = SOURCE.index(marker)
    opening = SOURCE.index("{", start)
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening, len(SOURCE)):
        char = SOURCE[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return SOURCE[opening:index + 1]
    raise AssertionError(f"unterminated function: {name}")


class Vp3ReferenceDiagnosticTests(unittest.TestCase):
    def test_mismatch_log_precedes_and_preserves_identity_assertion(self) -> None:
        body = function_body("nouveau_vp3_fill_picparm_h264_vp")
        mismatch = body.index("NOUVEAU_DIAG_VP3_REF_MISMATCH")
        assertion = body.index(
            "assert(dec->refs[refs[j]->valid_ref].vidbuf == refs[j]);"
        )
        self.assertLess(mismatch, assertion)
        self.assertEqual(body.count(
            "assert(dec->refs[refs[j]->valid_ref].vidbuf == refs[j]);"
        ), 1)

    def test_slot_lookup_is_guarded_and_bound_assert_precedes_identity_assert(self) -> None:
        body = function_body("nouveau_vp3_fill_picparm_h264_vp")
        guarded_read = (
            "ref_slot < ref_slot_count ? "
            "dec->refs[ref_slot].vidbuf : NULL"
        )
        self.assertIn(guarded_read, body)
        self.assertIn("ref_slot >= ref_slot_count || slot_buffer != refs[j]", body)
        bound_assert = body.index("assert(ref_slot < ref_slot_count);")
        identity_assert = body.index(
            "assert(dec->refs[refs[j]->valid_ref].vidbuf == refs[j]);"
        )
        self.assertLess(bound_assert, identity_assert)

    def test_log_is_conditional_on_reference_slot_mismatch(self) -> None:
        body = function_body("nouveau_vp3_fill_picparm_h264_vp")
        condition = body.index(
            "if (ref_slot >= ref_slot_count || slot_buffer != refs[j])"
        )
        log = body.index("NOUVEAU_DIAG_VP3_REF_MISMATCH")
        self.assertLess(condition, log)
        self.assertIn("_debug_printf(\"NOUVEAU_DIAG_VP3_REF_MISMATCH", body)
        self.assertNotRegex(body, r"(?<!_)debug_printf\s*\(")
        for field in (
            "frame_num=%u",
            "ref_list_index=%u",
            "compact_ref_index=%u",
            "valid_ref=%u",
            "slot_buffer=%p",
            "fence_seq=%u",
        ):
            self.assertIn(field, body)


if __name__ == "__main__":
    unittest.main()
