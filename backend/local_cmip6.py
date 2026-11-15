import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from netcdf_lock import NC_LOCK as _io_lock

LOCAL_DIR = Path(os.getenv("LOCAL_CMIP6_DIR", Path(__file__).resolve().parent / "data" / "annual"))
LOCAL_MODEL = os.getenv("LOCAL_CMIP6_MODEL", "GFDL-ESM4")
VARIABLES = {"precipitation": "pr_m", "min_temp": "tasmin_m", "max_temp": "tasmax_m"}
YEARLY_VARIABLES = {"rx1day": "rx1day", "hotdays35": "hotdays35"}
SEARCH_CELLS = 2


def file_for(exp: str, year: int) -> Path:
    return LOCAL_DIR / f"{exp}_{year}.nc"


def has_years(exp: str, years: list[int]) -> bool:
    return LOCAL_DIR.is_dir() and all(file_for(exp, y).exists() for y in years)


@lru_cache(maxsize=1)
def bounds() -> tuple[float, float, float, float] | None:
    sample = next(iter(sorted(LOCAL_DIR.glob("*.nc"))), None) if LOCAL_DIR.is_dir() else None
    if sample is None:
        return None
    with _io_lock, xr.open_dataset(sample) as ds:
        return float(ds.lat.min()), float(ds.lat.max()), float(ds.lon.min()), float(ds.lon.max())


def covers(lat: float, lng: float) -> bool:
    b = bounds()
    if b is None:
        return False
    lon = lng % 360
    return b[0] - 0.125 <= lat <= b[1] + 0.125 and b[2] - 0.125 <= lon <= b[3] + 0.125


def _nearest_land_cell(da: xr.DataArray, lat: float, lon: float) -> tuple[float, float] | None:
    step = float(abs(da.lat[1] - da.lat[0]))
    win = da.isel(month=0) if "month" in da.dims else da
    win = win.sel(lat=slice(lat - SEARCH_CELLS * step, lat + SEARCH_CELLS * step),
                  lon=slice(lon - SEARCH_CELLS * step, lon + SEARCH_CELLS * step))
    pts = win.to_dataframe(name="v").reset_index().dropna(subset=["v"])
    if pts.empty:
        return None
    d = (pts["lat"] - lat) ** 2 + ((pts["lon"] - lon) * np.cos(np.radians(lat))) ** 2
    best = pts.loc[d.idxmin()]
    return float(best["lat"]), float(best["lon"])


@lru_cache(maxsize=512)
def _cell(exp: str, lat: float, lng: float) -> tuple[float, float] | None:
    sample = next(iter(sorted(LOCAL_DIR.glob(f"{exp}_*.nc"))), None)
    if sample is None:
        return None
    with _io_lock, xr.open_dataset(sample) as ds:
        return _nearest_land_cell(ds["pr_m"].load(), lat, lng % 360)


def monthly_point(exp: str, variable: str, lat: float, lng: float, years: list[int]) -> pd.Series:
    name = VARIABLES[variable]
    cell = _cell(exp, round(lat, 3), round(lng, 3))
    if cell is None:
        raise ValueError("No land data near this location in the local climate files.")
    values, index = [], []
    for y in years:
        with _io_lock, xr.open_dataset(file_for(exp, y)) as ds:
            v = ds[name].sel(lat=cell[0], lon=cell[1]).values.astype(float)
        values.extend(v)
        index.extend(pd.date_range(f"{y}-01-01", periods=12, freq="MS"))
    return pd.Series(values, index=pd.DatetimeIndex(index)).dropna()


def yearly_from_monthly(monthly: pd.Series, variable: str) -> pd.Series:
    g = monthly.groupby(monthly.index.year)
    yearly = g.sum(min_count=12) if variable == "precipitation" else g.mean()
    yearly = yearly[g.count() == 12]
    yearly.index = pd.to_datetime(yearly.index.astype(str) + "-01-01")
    return yearly


def yearly_point(exp: str, variable: str, lat: float, lng: float, years: list[int]) -> pd.Series:
    name = YEARLY_VARIABLES[variable]
    cell = _cell(exp, round(lat, 3), round(lng, 3))
    if cell is None:
        raise ValueError("No land data near this location in the local climate files.")
    out = {}
    for y in years:
        with _io_lock, xr.open_dataset(file_for(exp, y)) as ds:
            out[y] = float(ds[name].sel(lat=cell[0], lon=cell[1]).values)
    s = pd.Series(out, dtype=float).dropna()
    s.index = pd.to_datetime(s.index.astype(str) + "-01-01")
    return s
