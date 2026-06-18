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
            "sp_local_gov_k12": "PASS",
            "sp_water_sewer": "PASS",
            "sp_community_college_go": "PASS",
        }
        actual_status = dict(zip(regression["methodology_id"], regression["status"]))
        self.assertEqual(actual_status, expected_status)
        self.assertFalse(regression["bad_formula_ids"].fillna("").astype(str).str.strip().any())

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
