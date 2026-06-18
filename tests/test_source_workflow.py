from __future__ import annotations

import unittest

from utils.source_workflow import _auto_sheet, _clean_input_value, _values_match


class SourceWorkflowTests(unittest.TestCase):
    def test_single_sheet_workbook_auto_selects_sheet(self) -> None:
        self.assertEqual(_auto_sheet(["Sheet1"], "Financials_ West Sacramento.xlsx"), "Sheet1")

    def test_multi_sheet_workbook_requires_a_raw_or_matching_hint(self) -> None:
        self.assertIsNone(_auto_sheet(["Summary", "Scorecard"], "Financials_ West Sacramento.xlsx"))

    def test_editor_list_string_round_trips_as_list(self) -> None:
        self.assertEqual(_clean_input_value("[1, 2.5, 3]"), [1.0, 2.5, 3.0])
        self.assertTrue(_values_match("[1, 2.5, 3]", [1.0, 2.5, 3.0]))
        self.assertEqual(_clean_input_value("(1,200)"), -1200.0)


if __name__ == "__main__":
    unittest.main()
