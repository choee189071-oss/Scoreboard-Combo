from __future__ import annotations

import unittest

from engine.regression_engine import (
    run_raw_validation_regression,
    run_synthetic_clean_data_regression,
)


class RegressionBaselineTests(unittest.TestCase):
    def test_synthetic_clean_data_baseline(self) -> None:
        regression = run_synthetic_clean_data_regression()

        expected_status = {
            "moodys_ccd_go": "PASS",
            "moodys_k12": "PASS",
            "sp_local_gov_k12": "FAIL",
            "sp_water_sewer": "PASS",
            "sp_community_college_go": "FAIL",
        }
        actual_status = dict(zip(regression["methodology_id"], regression["status"]))
        self.assertEqual(actual_status, expected_status)

        known_bad_formulas = {
            "sp_local_gov_k12": {
                "gdp_per_capita_ratio",
                "personal_income_ratio",
                "net_direct_debt_per_capita",
                "npl_per_capita",
            },
            "sp_community_college_go": {
                "gdp_per_capita",
                "personal_income_per_capita",
            },
        }
        failed_rows = regression.set_index("methodology_id").loc[known_bad_formulas.keys()]
        for methodology_id, expected_ids in known_bad_formulas.items():
            bad_ids = {
                item.strip()
                for item in str(failed_rows.loc[methodology_id, "bad_formula_ids"]).split(";")
                if item.strip()
            }
            self.assertEqual(bad_ids, expected_ids)

    def test_raw_validation_regression_baseline(self) -> None:
        regression = run_raw_validation_regression()

        self.assertEqual(set(regression["status"]), {"ok"})
        self.assertEqual(len(regression), 12)

        official = regression[regression["scoring_mode"].eq("official_assisted")]
        rating_matches = official.set_index("fixture_key")["rating_match"].to_dict()
        self.assertEqual(
            rating_matches,
            {
                "alum_rock_moodys_k12": False,
                "contra_costa_moodys_ccd_go": True,
                "contra_costa_sp_community_college_go": False,
                "jefferson_sp_local_gov_k12": True,
                "ontario_sp_water_sewer": True,
                "west_sacramento_sp_local_gov_k12": True,
            },
        )

        score_matches = official.set_index("fixture_key")["score_match"].to_dict()
        self.assertEqual(
            score_matches,
            {
                "alum_rock_moodys_k12": False,
                "contra_costa_moodys_ccd_go": False,
                "contra_costa_sp_community_college_go": False,
                "jefferson_sp_local_gov_k12": False,
                "ontario_sp_water_sewer": True,
                "west_sacramento_sp_local_gov_k12": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
