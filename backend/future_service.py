"""Statistical tests for future projections, from the year-by-year files in data/annual.

The projections page itself uses the 20-year summaries served by climate_projections_api.
Those have one value per period, which is too few for trend or Mann-Whitney tests, so the
tests here use one value per year: from the yearly files in data/annual when they cover the
period and the farm, otherwise fetched from NASA NCCS.
"""
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import local_cmip6
import nasa_fallback
from comparison_stats import compare_scenarios
from descriptive_stats import mann_kendall_sens_slope, regression_line

SCENARIOS = {"ssp126": "Best case (SSP1-2.6)", "ssp585": "Worst case (SSP5-8.5)"}
TEST_VARIABLES = {"precipitation", "min_temp", "max_temp", "rx1day", "hotdays35"}


def _yearly(exp: str, variable: str, lat: float, lng: float, years: list[int]) -> pd.Series:
    if variable in local_cmip6.YEARLY_VARIABLES:
        return local_cmip6.yearly_point(exp, variable, lat, lng, years)
    monthly = local_cmip6.monthly_point(exp, variable, lat, lng, years)
    return local_cmip6.yearly_from_monthly(monthly, variable)


def scenario_tests(variable: str, start_year: int, end_year: int, lat: float, lng: float) -> dict:
    years = list(range(start_year, end_year + 1))
    local = all(local_cmip6.has_years(s, years) for s in SCENARIOS) and local_cmip6.covers(lat, lng)
    if local:
        series = {s: _yearly(s, variable, lat, lng, years) for s in SCENARIOS}
        source = "local"
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {s: pool.submit(nasa_fallback.yearly_series, s, variable, lat, lng, years) for s in SCENARIOS}
            series = {s: f.result() for s, f in futures.items()}
        source = "nasa"
    a, b = series["ssp126"], series["ssp585"]
    common = a.index.intersection(b.index)
    return {
        "available": True,
        "source": source,
        "years": [start_year, end_year],
        "comparison": compare_scenarios(a[common], b[common], SCENARIOS["ssp126"], SCENARIOS["ssp585"]),
        "trends": {
            s: {"mann_kendall_sens_slope": mann_kendall_sens_slope(v, "yearly"),
                "regression_line": regression_line(v, "yearly")}
            for s, v in series.items()
        },
    }
