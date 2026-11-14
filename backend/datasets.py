"""Farmer-friendly variables mapped to real NASA data sources.

Historical data comes from the Giovanni Area-Averaged Time Series service, reached
through NASA Harmony. Every `giovanni_var` below appears in Giovanni's list of
configured variables: https://api.giovanni.earthdata.nasa.gov/configured-variables/

Future data comes from NEX-GDDP-CMIP6, which Giovanni does not serve. It is read
from the NASA Center for Climate Simulation (NCCS) THREDDS server instead.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    short_name: str        # CMR collection short name (concept ID is looked up at runtime)
    version: str           # CMR collection version
    giovanni_var: str      # Giovanni variable ID passed to Harmony
    step: str              # native time step: "3H", "daily" or "monthly"
    grid_deg: tuple        # (lon, lat) grid size in degrees, used to widen tiny farm boxes
    first_date: str        # earliest data available (YYYY-MM-DD)


# kind controls unit conversion and how values are combined over time:
#   "flux"  -> converted to mm/day, then summed into monthly / yearly totals (rain, ET)
#   "temp"  -> converted to °C, averaged
#   "soil"  -> converted to % volumetric water content, averaged
HISTORICAL = {
    "precipitation": {
        "label": "Precipitation",
        "kind": "flux",
        "sources": {
            "daily": Source("GPM_3IMERGDF", "07", "GPM_3IMERGDF_07_precipitation", "daily", (0.1, 0.1), "1998-01-01"),
            "monthly": Source("GPM_3IMERGM", "07", "GPM_3IMERGM_07_precipitation", "monthly", (0.1, 0.1), "1998-01-01"),
        },
        "about": "NASA GPM IMERG satellite rainfall estimates, about 10 km grid.",
    },
    "soil_moisture": {
        "label": "Soil Moisture (top 10 cm)",
        "kind": "soil",
        "soil_layer_m": 0.10,
        "sources": {
            "daily": Source("GLDAS_NOAH025_3H", "2.1", "GLDAS_NOAH025_3H_2_1_SoilMoi0_10cm_inst", "3H", (0.25, 0.25), "2000-01-01"),
            "monthly": Source("GLDAS_NOAH025_M", "2.1", "GLDAS_NOAH025_M_2_1_SoilMoi0_10cm_inst", "monthly", (0.25, 0.25), "2000-01-01"),
        },
        "about": "NASA GLDAS Noah land surface model, about 25 km grid.",
    },
    "air_temp": {
        "label": "Air Temperature",
        "kind": "temp",
        "sources": {
            "daily": Source("M2SDNXSLV", "5.12.4", "M2SDNXSLV_5_12_4_T2MMEAN", "daily", (0.625, 0.5), "1980-01-01"),
            "monthly": Source("GLDAS_NOAH025_M", "2.1", "GLDAS_NOAH025_M_2_1_Tair_f_inst", "monthly", (0.25, 0.25), "2000-01-01"),
        },
        "about": "Daily: NASA MERRA-2 (about 50 km). Monthly/yearly: NASA GLDAS (about 25 km).",
    },
    "evapotranspiration": {
        "label": "Total Land Evapotranspiration",
        "kind": "flux",
        "sources": {
            "daily": Source("GLDAS_NOAH025_3H", "2.1", "GLDAS_NOAH025_3H_2_1_Evap_tavg", "3H", (0.25, 0.25), "2000-01-01"),
            "monthly": Source("GLDAS_NOAH025_M", "2.1", "GLDAS_NOAH025_M_2_1_Evap_tavg", "monthly", (0.25, 0.25), "2000-01-01"),
        },
        "about": "Water lost from soil and plants. NASA GLDAS Noah, about 25 km grid.",
    },
    "min_temp": {
        "label": "Minimum Temperature",
        "kind": "temp",
        # MERRA-2 daily statistics: the true daily minimum. Monthly/yearly = average of daily minimums.
        "sources": {
            "daily": Source("M2SDNXSLV", "5.12.4", "M2SDNXSLV_5_12_4_T2MMIN", "daily", (0.625, 0.5), "1980-01-01"),
        },
        "about": "Daily minimum 2 m air temperature from NASA MERRA-2, about 50 km grid.",
    },
    "max_temp": {
        "label": "Maximum Temperature",
        "kind": "temp",
        "sources": {
            "daily": Source("M2SDNXSLV", "5.12.4", "M2SDNXSLV_5_12_4_T2MMAX", "daily", (0.625, 0.5), "1980-01-01"),
        },
        "about": "Daily maximum 2 m air temperature from NASA MERRA-2, about 50 km grid.",
    },
}

# Daily requests pull many more time steps; keep them short so jobs finish quickly
# and don't use up the Giovanni monthly compute quota.
MAX_DAILY_RANGE_DAYS = 731


def pick_source(variable: str, frequency: str) -> Source:
    """Choose the lightest source that can give the requested frequency."""
    sources = HISTORICAL[variable]["sources"]
    if frequency in ("monthly", "yearly") and "monthly" in sources:
        return sources["monthly"]
    return sources["daily"]


# ---------------- Future (NEX-GDDP-CMIP6) ----------------

FUTURE_VARIABLES = {
    "precipitation": {"label": "Precipitation", "cmip_var": "pr", "kind": "flux"},
    "min_temp": {"label": "Minimum Temperature", "cmip_var": "tasmin", "kind": "temp"},
    "max_temp": {"label": "Maximum Temperature", "cmip_var": "tasmax", "kind": "temp"},
}

SSP_SCENARIOS = {
    "ssp126": "SSP1-2.6: low emissions, strong climate action",
    "ssp245": "SSP2-4.5: middle of the road",
    "ssp370": "SSP3-7.0: high emissions",
    "ssp585": "SSP5-8.5: very high emissions",
}

# Three well-tested models with the r1i1p1f1 run for all four SSPs.
# Results show the average of the three, plus their lowest and highest values.
FUTURE_MODELS = [
    ("ACCESS-CM2", "r1i1p1f1"),
    ("MRI-ESM2-0", "r1i1p1f1"),
    ("GFDL-ESM4", "r1i1p1f1"),
]
FUTURE_FIRST_YEAR = 2015
FUTURE_LAST_YEAR = 2100
FUTURE_GRID_DEG = 0.25