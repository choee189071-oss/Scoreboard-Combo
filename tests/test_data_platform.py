from __future__ import annotations

import unittest

from engine.data_platform import build_data_platform_tables


class DataPlatformTests(unittest.TestCase):
    def test_data_platform_catalogs_build(self) -> None:
        tables = build_data_platform_tables()

        self.assertGreaterEqual(len(tables["source_catalog"]), 10)
        self.assertGreaterEqual(len(tables["field_dictionary"]), 50)
        self.assertGreaterEqual(len(tables["source_field_matrix"]), 100)
        self.assertGreaterEqual(len(tables["methodology_field_matrix"]), 50)

    def test_active_methodology_fields_have_core_metadata(self) -> None:
        field_dictionary = build_data_platform_tables()["field_dictionary"]
        used = field_dictionary[field_dictionary["used_by_methodology"].astype(bool)]

        self.assertFalse(
            used["readiness_status"].isin(
                {
                    "dictionary_missing",
                    "source_priority_missing",
                    "alias_mapping_missing",
                }
            ).any()
        )


if __name__ == "__main__":
    unittest.main()
