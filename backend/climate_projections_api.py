"""Climate API for the map frontend.

Mounted by main.py at /api/projections.

Endpoints
    GET  /meta                    available scenarios, periods and variables
    GET  /point?lat=..&lon=..     values at one clicked location
    POST /area                    area average inside a drawn polygon
                                  body: {"geometry": <GeoJSON Polygon>}
"""
import os
from pathlib import Path

import numpy as np
import shapely
import xarray as xr
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from shapely.geometry import shape

from netcdf_lock import NC_LOCK

STATS_DIR = Path(os.environ.get("CLIMATE_STATS", Path(__file__).resolve().parent / "data" / "stats"))
LAT, LON = (-12, 38), (60, 145)

SCALARS = ["pr_annual", "tasmin_annual", "tasmax_annual", "rx1day", "hotdays35"]
MONTHLY = ["pr_monthly", "tasmin_monthly", "tasmax_monthly"]

app = FastAPI(title="Climate projections (GFDL-ESM4, NEX-GDDP-CMIP6)")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])

_DS: dict | None = None


def datasets() -> dict:
    global _DS
    with NC_LOCK:
        if _DS is None:
            loaded = {}
            for p in sorted(STATS_DIR.glob("*.nc")):
                if p.stem.startswith("delta_"):
                    continue
                with xr.open_dataset(p) as src:
                    loaded[p.stem] = src[SCALARS + MONTHLY].load()
            if "baseline" not in loaded:
                raise HTTPException(503, f"Climate summary files not found on the server ({STATS_DIR.name}/baseline.nc).")
            _DS = loaded
        return _DS


def _projection_keys():
    return [k for k in datasets() if k != "baseline"]


def point_summary(ds, lat, lon):
    p = ds.sel(lat=lat, lon=lon, method="nearest")
    if np.isnan(float(p["tasmax_annual"])):
        return None
    out = {v: round(float(p[v]), 1) for v in SCALARS}
    out.update({v: [round(float(x), 1) for x in p[v].values] for v in MONTHLY})
    return out


def area_summary(ds, geom):
    lon2d, lat2d = np.meshgrid(ds.lon.values, ds.lat.values)
    inside = xr.DataArray(shapely.contains_xy(geom, lon2d, lat2d),
                          coords={"lat": ds.lat, "lon": ds.lon}, dims=("lat", "lon"))
    sub = ds.where(inside)
    n = int(sub["tasmax_annual"].notnull().sum())
    if n == 0:
        return None
    m = sub.weighted(np.cos(np.deg2rad(ds.lat))).mean(["lat", "lon"])
    out = {v: round(float(m[v]), 1) for v in SCALARS}
    out.update({v: [round(float(x), 1) for x in m[v].values] for v in MONTHLY})
    out["n_cells"] = n
    return out


@app.get("/meta")
def meta():
    return {"region": {"lat": LAT, "lon": LON},
            "projections": _projection_keys(),
            "scalars": SCALARS, "monthly": MONTHLY}


@app.get("/point")
def point(lat: float, lon: float):
    if not (LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]):
        raise HTTPException(400, "Outside the supported region")
    ds = datasets()
    base = point_summary(ds["baseline"], lat, lon)
    if base is None:
        raise HTTPException(404, "No data for this location (ocean)")
    return {"mode": "point", "lat": lat, "lon": lon, "baseline": base,
            "projections": {k: point_summary(ds[k], lat, lon) for k in _projection_keys()}}


class AreaRequest(BaseModel):
    geometry: dict


@app.post("/area")
def area(req: AreaRequest):
    try:
        geom = shape(req.geometry)
    except Exception:
        raise HTTPException(400, "Could not read geometry (expected a GeoJSON Polygon)")
    if not geom.is_valid:
        raise HTTPException(400, "Invalid shape")
    minx, miny, maxx, maxy = geom.bounds
    if minx < LON[0] or maxx > LON[1] or miny < LAT[0] or maxy > LAT[1]:
        raise HTTPException(400, "Shape extends outside the supported region")

    ds = datasets()
    base = area_summary(ds["baseline"], geom)
    if base is None:
        c = geom.centroid
        base = point_summary(ds["baseline"], c.y, c.x)
        if base is None:
            raise HTTPException(404, "No data here (ocean)")
        return {"mode": "centroid", "baseline": base,
                "projections": {k: point_summary(ds[k], c.y, c.x) for k in _projection_keys()}}
    return {"mode": "area", "baseline": base,
            "projections": {k: area_summary(ds[k], geom) for k in _projection_keys()}}
