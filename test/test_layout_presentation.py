"""U2 공용 배치 표시 함수(Qt 없음)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio.presentation import telegram_pattern, negative_pattern, member_rows, byte_grid  # noqa: E402

LAYOUT = [
    {"kind": "literal", "label": "SID", "bit_offset": 0, "bits": 8, "value": "2E"},
    {"kind": "literal", "label": "DID", "bit_offset": 8, "bits": 16, "value": "F1 90"},
    {"kind": "data", "label": "Hi", "bit_offset": 24, "bits": 4, "encoding": "unsigned", "type_name": "U4"},
    {"kind": "data", "label": "Lo", "bit_offset": 28, "bits": 4, "encoding": "unsigned", "type_name": "U4", "constant_text": "3"},
    {"kind": "iteration", "label": "Items", "bit_offset": 32, "bits": 0, "min_items": 1, "item_bits": 8},
    {"kind": "data", "label": "Item", "bit_offset": 0, "bits": 8, "depth": 1, "encoding": "unsigned"},
]


class LayoutPresentationTest(unittest.TestCase):
    def test_pattern_groups_non_literal_fields(self):
        text, groups = telegram_pattern(LAYOUT)
        self.assertEqual(text, "2E F1 90 zz")
        # 자리표시 묶음은 연속한 최상위 비고정 필드 전부다: Hi, Lo와 반복 항목 Items(반복 안의 Item은 세지 않는다).
        self.assertEqual(groups, [("zz", "Hi", 3)])
        self.assertEqual(negative_pattern(0x2E), "7F 2E rc")
        self.assertEqual(telegram_pattern([{"kind": "error", "label": "x", "bit_offset": 0, "bits": 0}]), ("구조 오류", []))

    def test_member_rows_use_odx_bit_positions(self):
        rows = member_rows(LAYOUT)
        hi, lo = rows[2], rows[3]
        self.assertEqual((hi["byte"], hi["bit"]), ("3", "4"))
        self.assertEqual((lo["byte"], lo["bit"], lo["constant"]), ("3", "0", "3"))
        self.assertTrue(rows[5]["name"].strip().startswith("↻"))

    def test_byte_grid_msb_first_and_iteration_tail(self):
        grid = byte_grid(LAYOUT)
        self.assertEqual([g["byte"] for g in grid[:4]], [0, 1, 2, 3])
        self.assertEqual([c[0] for c in grid[3]["cells"]], ["Hi"] * 4 + ["Lo"] * 4)
        self.assertIsNone(grid[-1]["byte"])
        self.assertTrue(grid[-1]["cells"][0][0].startswith("Items"))


if __name__ == "__main__":
    unittest.main()
