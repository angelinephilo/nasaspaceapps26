"""Future projections: NEX-GDDP-CMIP6 point values from the NCCS THREDDS server.

Giovanni does not serve NEX-GDDP-CMIP6, so we use the NetCDF Subset Service (NCSS)
that NCCS documents for this dataset. Each file holds one year of daily data for one
model / scenario / variable. We ask NCSS for the grid cell nearest the farm (CSV),
then turn daily values into yearly averages or totals.

File names differ between dataset versions (e.g. `_v1.1`, `_v1.2` suffixes), so we
read each folder's THREDDS catalog and pick the newest version of each year's file.
"""
import re
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import pandas as pd
import requests

from datasets import FUTURE_GRID_DEG, FUTURE_MODELS, FUTURE_VARIABLES, SSP_SCENARIOS
from processing import parse_ncss_point_csv, summarize, to_base_units, unit_label

THREDDS = "https://ds.nccs.nasa.gov/thredds"
DATA_ROOT = "AMES/NEX/GDDP-CMIP6"
_FILE_RE = re.compile(r"_(\d{4})(?:_v(\d+(?:\.\d+)*))?\.nc$")
_session = requests.Session()
_session.headers["User-Agent"] = "AgriClimateExplorer/1.0 (NASA Space Apps)"


@lru_cache(maxsize=256)
def year_files(model: str, run: str, ssp: str, var: str) -> dict:
    """{year: urlPath} for one model/scenario/variable folder, newest version per year."""
    url = f"{THREDDS}/catalog/{DATA_ROOT}/{model}/{ssp}/{run}/{var}/catalog.xml"
    r = _session.get(url, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    best: dict[int, tuple[tuple, str]] = {}
    for el in root.iter():
        if not el.tag.endswith("dataset"):
            continue
        path = el.get("urlPath")
        if not path:
            continue
        m = _FILE_RE.search(path)
        if not m:
            continue
        year = int(m.group(1))
        version = tuple(int(x) for x in (m.group(2) or "0").split("."))
        if year not in best or version > best[year][0]:
            best[year] = (version, path)
    return {y: p for y, (_, p) in best.items()}


def fetch_year(url_path: str, var: str, lat: float, lng: float, year: int) -> tuple[str | None, pd.Series]:
    params = {
        "var": var,
        "latitude": f"{lat:.4f}",
        "longitude": f"{lng:.4f}",
        "time_start": f"{year}-01-01T00:00:00Z",
        "time_end": f"{year}-12-31T23:59:59Z",
        "accept": "csv",
    }
    last_err = None
    for _ in range(3):
        try:
            r = _session.get(f"{THREDDS}/ncss/grid/{url_path}", params=params, timeout=90)
            r.raise_for_status()
            return parse_ncss_point_csv(r.text)
        except Exception as e:  # retry flaky requests
            last_err = e
    raise RuntimeError(f"NCCS request failed for {url_path}: {last_err}")


def model_yearly(model: str, run: str, ssp: str, var: str, kind: str, lat: float, lng: float, years: list[int]) -> pd.Series:
    files = year_files(model, run, ssp, var)
    out = {}
    for y in years:
        if y not in files:
            continue
        unit, daily = fetch_year(files[y], var, lat, lng, y)
        daily = to_base_units(daily, unit, kind, "kg m-2 s-1" if kind == "flux" else "K")
        out[y] = daily.sum() / len(daily) * 365.25 if kind == "flux" else daily.mean()
    return pd.Series(out, dtype=float)


_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def projection(variable: str, ssp: str, start_year: int, end_year: int, lat: float, lng: float) -> dict:
    meta = FUTURE_VARIABLES[variable]
    # Snap to the 0.25° grid so nearby farms share cached results
    lat_s = round(round(lat / FUTURE_GRID_DEG) * FUTURE_GRID_DEG, 3)
    lng_s = round(round(lng / FUTURE_GRID_DEG) * FUTURE_GRID_DEG, 3)
    key = (variable, ssp, start_year, end_year, lat_s, lng_s)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    years = list(range(start_year, end_year + 1))
    var, kind = meta["cmip_var"], meta["kind"]

    errors = []
    # Read each model's file list once, then fetch one year per task in parallel
    with ThreadPoolExecutor(max_workers=12) as pool:
        catalogs = {}
        for (m, r), fut in [((m, r), pool.submit(year_files, m, r, ssp, var)) for m, r in FUTURE_MODELS]:
            try:
                fut.result()
                catalogs[m] = r
            except Exception as e:
                errors.append(f"{m}: {e}")

        def one(model_run_year):
            model, run, y = model_run_year
            return model, model_yearly(model, run, ssp, var, kind, lat, lng, [y])

        tasks = [(m, r, y) for m, r in catalogs.items() for y in years]
        per_model: dict[str, list[pd.Series]] = {m: [] for m in catalogs}
        for fut in [pool.submit(one, t) for t in tasks]:
            try:
                model, s = fut.result()
                per_model[model].append(s)
            except Exception as e:
                errors.append(str(e))

    table = pd.DataFrame({m: pd.concat(parts) if parts else pd.Series(dtype=float) for m, parts in per_model.items()})
    table = table.dropna(how="all").sort_index()
    if table.empty:
        raise RuntimeError("Could not get climate projections from NASA NCCS. " + (errors[0] if errors else ""))

    ens = table.mean(axis=1)
    df = pd.DataFrame({"value": ens, "complete": True})
    df.index = pd.to_datetime(df.index.astype(str) + "-01-01")
    result = {
        "status": "successful",
        "variable": variable,
        "label": meta["label"],
        "frequency": "yearly",
        "unit": unit_label(kind, "yearly"),
        "scenario": ssp,
        "scenario_label": SSP_SCENARIOS[ssp],
        "models": list(table.columns),
        "source": "NASA NEX-GDDP-CMIP6 downscaled climate projections (about 25 km), average of "
                  f"{len(table.columns)} climate models.",
        "data": [
            {"date": str(y), "value": _r(row.mean()), "low": _r(row.min()), "high": _r(row.max())}
            for y, row in table.iterrows()
        ],
        "summary": summarize(df, "yearly"),
        "warnings": errors[:3],
    }
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > 100:
            _cache.popitem(last=False)
    return result


def _r(x):
    return None if pd.isna(x) else round(float(x), 2)