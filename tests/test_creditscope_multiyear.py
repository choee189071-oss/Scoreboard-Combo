from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from connectors.creditscope_loader import load_creditscope_source_candidates


class CreditScopeMultiYearTests(unittest.TestCase):
    def test_exact_row_mapping_reads_three_year_series(self) -> None:
        workbook = Workbook()
        ws = workbook.active
        ws.title = "Sheet1"
        rows = [
            ("Population", 55711, None, None),
            ("Total Governmental Revenue (from all Governmental Funds)", 100, 90, 80),
            ("Total Governmental Expenditure (from all Governmental Funds)", 95, 85, 75),
            ("Committed to", 10, 9, 8),
            ("Assigned", 1, 2, 3),
            ("Unassigned", 4, 5, 6),
        ]
        for row_idx, row in enumerate(rows, start=1):
            for col_idx, value in enumerate(row, start=1):
                ws.cell(row_idx, col_idx).value = value

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "creditscope.xlsx"
            workbook.save(path)
            result = load_creditscope_source_candidates(
                path,
                required_fields=[
                    "governmental_revenue",
                    "governmental_expense",
                    "committed_fund_balance",
                    "assigned_fund_balance",
                    "unassigned_fund_balance",
                    "reserve_revenue",
                ],
            )

        issuer_data = result["issuer_data"]
        self.assertEqual(issuer_data["governmental_revenue"], [100000.0, 90000.0, 80000.0])
        self.assertEqual(issuer_data["governmental_expense"], [95000.0, 85000.0, 75000.0])
        self.assertEqual(issuer_data["committed_fund_balance"], [10000.0, 9000.0, 8000.0])

        report = result["match_report"]
        revenue = report[report["field_name"].eq("governmental_revenue")].iloc[0]
        self.assertEqual(revenue["match_method"], "exact_row_mapping_series")
        self.assertEqual(revenue["matched_column"], "Sheet1!B2:D2")


if __name__ == "__main__":
    unittest.main()
