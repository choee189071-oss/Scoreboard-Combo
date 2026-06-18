from __future__ import annotations

from unittest import mock
import unittest

from connectors.census_api import CensusApiError, fetch_acs5_county_fields


class ApiConnectorTests(unittest.TestCase):
    def test_census_requires_configured_api_key(self) -> None:
        with mock.patch("connectors.census_api.get_census_api_key", return_value=None):
            with self.assertRaisesRegex(CensusApiError, "CENSUS_API_KEY is not configured"):
                fetch_acs5_county_fields(
                    state_fips="06",
                    county_fips="113",
                    fields=["population"],
                    year=2024,
                )


if __name__ == "__main__":
    unittest.main()
