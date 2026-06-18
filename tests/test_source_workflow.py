from __future__ import annotations

import unittest

from utils.source_workflow import _auto_sheet


class SourceWorkflowTests(unittest.TestCase):
    def test_single_sheet_workbook_auto_selects_sheet(self) -> None:
        self.assertEqual(_auto_sheet(["Sheet1"], "Financials_ West Sacramento.xlsx"), "Sheet1")

    def test_multi_sheet_workbook_requires_a_raw_or_matching_hint(self) -> None:
        self.assertIsNone(_auto_sheet(["Summary", "Scorecard"], "Financials_ West Sacramento.xlsx"))


if __name__ == "__main__":
    unittest.main()
