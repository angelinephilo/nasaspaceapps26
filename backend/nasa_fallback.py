"""Future projections fetched directly from NASA NCCS, for farms outside the pre-processed region.

Same model as the pre-processed files (NEX-GDDP-CMIP6, GFDL-ESM4, r1i1p1f1). Results have the
same shape as climate_projections_api (/point), but only for the requested variable, and each
period is estimated from a few evenly spaced sample years to keep the number of requests small.
"""
import re
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

from processing import parse_ncss_point_csv, to_base_units

THREDDS = "https://ds.nccs.nasa.gov/thredds"
DATA_ROOT = "AMES/NEX/GDDP-CMIP6"
MODEL, RUN = "GFDL-ESM4", "r1i1p1f1"
GRID_DEG = 0.25
SAMPLE_YEARS = 5
BASELINE = ("historical", 1995, 2014)
SCENARIOS = ("ssp126", "ssp585")
PERIODS = {"2026-2040": (2026, 2040), "2041-2060": (2041, 2060), "2061-2080": (2061, 2080), "2081-2100": (2081, 2100)}

SOURCE_VAR = {"precipitation": "pr", "rx1day": "pr", "min_temp": "tasmin", "max_temp": "tasmax", "hotdays35": "tasmax"}
FIELDS = {
    "pr": {"annual": "pr_annual", "monthly": "pr_monthly", "extra": ["rx1day"]},
    "tasmin": {"annual": "tasmin_annual", "monthly": "tasmin_monthly", "extra": []},
    "tasmax": {"annual": "tasmax_annual", "monthly": "tasmax_monthly", "extra": ["hotdays35"]},
}

_FILE_RE = re.compile(r"_(\d{4})(?:_v(\d+(?:\.\d+)*))?\.nc$")
_session = requests.Session()
_session.headers["User-Agent"] = "AgriClimateExplorer/1.0 (NASA Space Apps)"


@lru_cache(maxsize=32)
def year_files(exp: str, var: str) -> dict:
    url = f"{THREDDS}/catalog/{DATA_ROOT}/{MODEL}/{exp}/{RUN}/{var}/catalog.xml"
    r = _session.get(url, timeout=60)
    r.raise_for_status()
    best: dict[int, tuple[tuple, str]] = {}
    for el in ET.fromstring(r.content).iter():
        path = el.get("urlPath") if el.tag.endswith("dataset") else None
        m = _FILE_RE.search(path or "")
        if not m:
            continue
        year = int(m.group(1))
        version = tuple(int(x) for x in (m.group(2) or "0").split("."))
        if year not in best or version > best[year][0]:
            best[year] = (version, path)
    return {y: p for y, (_, p) in best.items()}


@lru_cache(maxsize=4096)
def year_stats(exp: str, var: str, lat: float, lng: float, year: int) -> dict:
    files = year_files(exp, var)
    if year not in files:
        raise RuntimeError(f"NASA has no {var} file for {exp} {year}.")
    params = {"var": var, "latitude": f"{lat:.3f}", "longitude": f"{lng:.3f}",
              "time_start": f"{year}-01-01T00:00:00Z", "time_end": f"{year}-12-31T23:59:59Z", "accept": "csv"}
    last = None
    for _ in range(3):
        try:
            r = _session.get(f"{THREDDS}/ncss/grid/{files[year]}", params=params, timeout=90)
            r.raise_for_status()
            unit, daily = parse_ncss_point_csv(r.text)
            break
        except Exception as e:
            last = e
    else:
        raise RuntimeError(f"NASA NCCS request failed ({exp} {var} {year}): {last}")
    kind = "flux" if var == "pr" else "temp"
    daily = to_base_units(daily, unit, kind, "kg m-2 s-1" if kind == "flux" else "K")
    by_month = daily.groupby(daily.index.month)
    monthly = by_month.sum() if kind == "flux" else by_month.mean()
    out = {"monthly": [float(monthly.get(m, np.nan)) for m in range(1, 13)]}
    if var == "pr":
        out["annual"] = float(daily.sum())
        out["rx1day"] = float(daily.max())
    else:
        out["annual"] = float(daily.mean())
        if var == "tasmax":
            out["hotdays35"] = float((daily > 35).sum())
    return out


def sample_years(start: int, end: int, n: int = SAMPLE_YEARS) -> list[int]:
    return sorted({int(round(y)) for y in np.linspace(start, end, n)})


def snap(lat: float, lng: float) -> tuple[float, float]:
    return (round(round((lat - 0.125) / GRID_DEG) * GRID_DEG + 0.125, 3),
            round(round((lng - 0.125) / GRID_DEG) * GRID_DEG + 0.125, 3))


def _period_summary(var: str, items: list[dict]) -> dict:
    f = FIELDS[var]
    out = {f["annual"]: round(float(np.mean([i["annual"] for i in items])), 1),
           f["monthly"]: [round(float(x), 1) for x in np.nanmean([i["monthly"] for i in items], axis=0)]}
    for extra in f["extra"]:
        out[extra] = round(float(np.mean([i[extra] for i in items])), 1)
    return out


def _run_parallel(jobs: list[tuple]) -> dict:
    results, errors = {}, []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(year_stats, *job): job for job in jobs}
        for fut, job in futures.items():
            try:
                results[job] = fut.result()
            except Exception as e:
                errors.append(str(e))
    return results, errors


_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def summaries(variable: str, lat: float, lng: float) -> dict:
    var = SOURCE_VAR[variable]
    lat_s, lng_s = snap(lat, lng)
    key = (var, lat_s, lng_s)
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    groups = {"baseline": (BASELINE[0], sample_years(BASELINE[1], BASELINE[2]))}
    for s in SCENARIOS:
        for name, (a, b) in PERIODS.items():
            groups[f"{s}_{name}"] = (s, sample_years(a, b))
    jobs = [(exp, var, lat_s, lng_s, y) for exp, years in groups.values() for y in years]
    results, errors = _run_parallel(jobs)

    out = {"mode": "nasa", "lat": lat_s, "lon": lng_s, "sample_years": SAMPLE_YEARS, "projections": {}}
    for name, (exp, years) in groups.items():
        items = [results[(exp, var, lat_s, lng_s, y)] for y in years if (exp, var, lat_s, lng_s, y) in results]
        summary = _period_summary(var, items) if items else None
        if name == "baseline":
            out["baseline"] = summary
        else:
            out["projections"][name] = summary
    if not out.get("baseline") or np.isnan(out["baseline"][FIELDS[var]["annual"]]):
        raise RuntimeError("NASA had no data for this location (it may be over the sea). " + (errors[0] if errors else ""))
    out["warnings"] = errors[:3]
    with _cache_lock:
        _cache[key] = out
        while len(_cache) > 200:
            _cache.popitem(last=False)
    return out


def yearly_series(exp: str, variable: str, lat: float, lng: float, years: list[int]) -> pd.Series:
    var = SOURCE_VAR[variable]
    lat_s, lng_s = snap(lat, lng)
    field = "annual" if variable not in ("rx1day", "hotdays35") else variable
    results, errors = _run_parallel([(exp, var, lat_s, lng_s, y) for y in years])
    values = {y: results[(exp, var, lat_s, lng_s, y)][field] for y in years if (exp, var, lat_s, lng_s, y) in results}
    if len(values) < max(3, len(years) // 2):
        raise RuntimeError("Could not get enough yearly values from NASA. " + (errors[0] if errors else ""))
    s = pd.Series(values, dtype=float).sort_index()
    s.index = pd.to_datetime(s.index.astype(str) + "-01-01")
    return s
