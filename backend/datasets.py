HISTORICAL_DATASETS = {
    "precipitation": {
        "daily": {"collection_id": "C2723754864-GES_DISC", "variable": "precipitation"},
        "monthly": {"collection_id": "C2723754865-GES_DISC", "variable": "precipitation"},
    },
    "soil_moisture": {
        "daily": {"collection_id": "C2763266360-NSIDC_CPRD", "variable": "sm_rootzone"},
        "monthly": {"collection_id": "C2763266360-NSIDC_CPRD", "variable": "sm_rootzone"},
    },
    # GLDAS Noah v2.1 Monthly / 3-Hourly covers Air Temp, Min/Max Temp, and Evapotranspiration globally
    "air_temp": {
        "monthly": {"collection_id": "C1342986035-GES_DISC", "variable": "Tair_f_inst"},
    },
    "evapotranspiration": {
        "monthly": {"collection_id": "C1342986035-GES_DISC", "variable": "Evap_tavg"},
    },
    "min_temp": {
        "monthly": {"collection_id": "C1342986035-GES_DISC", "variable": "Tair_f_inst"},
    },
    "max_temp": {
        "monthly": {"collection_id": "C1342986035-GES_DISC", "variable": "Tair_f_inst"},
    },
}

# NEX-GDDP-CMIP6 (NASA Earth Exchange Global Daily Downscaled Projections)
# Note: Verify exact CMR Concept IDs for your chosen CMIP6 model + SSP combination via earthaccess.search_datasets()
FUTURE_SSP_DATASETS = {
    "ssp126": {"collection_id": "C2349352445-NCCS", "variables": {"precipitation": "pr", "min_temp": "tasmin", "max_temp": "tasmax"}},
    "ssp245": {"collection_id": "C2349352468-NCCS", "variables": {"precipitation": "pr", "min_temp": "tasmin", "max_temp": "tasmax"}},
    "ssp370": {"collection_id": "C2349352489-NCCS", "variables": {"precipitation": "pr", "min_temp": "tasmin", "max_temp": "tasmax"}},
    "ssp585": {"collection_id": "C2349352510-NCCS", "variables": {"precipitation": "pr", "min_temp": "tasmin", "max_temp": "tasmax"}},
}