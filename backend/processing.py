"""Pure data helpers: parse CSVs, convert units, aggregate, summarise. No network calls here."""
import io
import math
import re

import numpy as np
import pandas as pd

# ---------------- Geometry ----------------


def bbox_from_geometry(geometry: dict) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a GeoJSON Polygon or Point."""
    if geometry["type"] == "Point":
        lng, lat = geometry["coordinates"][:2]
        return lng, lat, lng, lat
    ring = geometry["coordinates"][0]
    lngs = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lngs), min(lats), max(lngs), max(lats)


def pad_bbox(bbox, grid_deg) -> tuple[float, float, float, float]:
    """Widen a box so it covers at least one grid cell of the dataset.

    Most farms are far smaller than a satellite/model grid cell (10-50 km),
    and Giovanni needs the box to contain at least one cell centre.
    """
    w, s, e, n = bbox
    gx, gy = grid_deg
    if e - w < gx:
        c = (w + e) / 2
        w, e = c - gx / 2, c + gx / 2
    if n - s < gy:
        c = (s + n) / 2
        s, n = c - gy / 2, c + gy / 2
    return (round(max(w, -180), 4), round(max(s, -90), 4), round(min(e, 180), 4), round(min(n, 90), 4))


def bbox_center(bbox) -> tuple[float, float]:
    w, s, e, n = bbox
    return (s + n) / 2, (w + e) / 2  # lat, lng


# ---------------- Units ----------------


def _norm_unit(unit: str | None) -> str:
    u = (unit or "").strip().lower()
    u = u.replace("**", "").replace("^", "").replace(" ", "").replace("·", "")
    return u


def to_base_units(values: pd.Series, unit: str | None, kind: str, default_unit: str, soil_layer_m: float = 0.1):
    """Convert to farmer units. flux -> mm/day, temp -> °C, soil -> % volume."""
    u = _norm_unit(unit) or _norm_unit(default_unit)
    if kind == "flux":
        if u in ("mm/hr", "mm/h", "mmhr-1", "mmh-1", "mm/hour"):
            return values * 24
        if u in ("mm/day", "mm/d", "mmday-1", "mmd-1"):
            return values
        if u in ("kgm-2s-1", "kg/m2/s", "kgm-2/s", "kg/m2s", "kg/(m2s)", "mm/s", "mms-1"):
            return values * 86400
        raise ValueError(f"Unexpected unit for rainfall/evapotranspiration: {unit!r}")
    if kind == "temp":
        if u in ("k", "kelvin", "degk"):
            return values - 273.15
        if u in ("c", "degc", "°c", "celsius", "deg_c"):
            return values
        raise ValueError(f"Unexpected unit for temperature: {unit!r}")
    if kind == "soil":
        if u in ("kgm-2", "kg/m2", "mm"):
            # kg/m² of water in a layer of depth d (m) -> m³/m³ = v / (1000 * d); x100 for %
            return values / (1000 * soil_layer_m) * 100
        if u in ("m3/m3", "m3m-3", "cm3/cm3", "cm3cm-3"):
            return values * 100
        raise ValueError(f"Unexpected unit for soil moisture: {unit!r}")
    raise ValueError(f"Unknown kind {kind}")


DEFAULT_UNITS = {
    # Units of each Giovanni variable as published; used when the CSV header doesn't say.
    "GPM_3IMERGDF_07_precipitation": "mm/day",
    "GPM_3IMERGM_07_precipitation": "mm/hr",
    "M2SDNXSLV_5_12_4_T2MMEAN": "K",
    "M2SDNXSLV_5_12_4_T2MMIN": "K",
    "M2SDNXSLV_5_12_4_T2MMAX": "K",
}


def default_unit_for(giovanni_var: str) -> str:
    if giovanni_var in DEFAULT_UNITS:
        return DEFAULT_UNITS[giovanni_var]
    if "Tair" in giovanni_var:
        return "K"
    if "Evap" in giovanni_var:
        return "kg m-2 s-1"
    if "SoilMoi" in giovanni_var:
        return "kg m-2"
    return ""


# ---------------- Giovanni CSV ----------------


def parse_giovanni_csv(text: str) -> tuple[dict, pd.Series]:
    """Parse the CSV from Giovanni's area-averaged time series service.

    Layout: `key,value` header lines, then a line that is just ",",
    then a table with columns `time,mean_<VARIABLE>`.
    """
    lines = text.splitlines()
    attrs, table_start = {}, None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == ",":
            table_start = i + 1
            break
        if stripped.lower().startswith("time,"):
            table_start = i
            break
        if "," in stripped:
            k, v = stripped.split(",", 1)
            attrs[k.strip()] = v.strip()
    if table_start is None:
        raise ValueError("Giovanni returned a CSV with no data table.")

    df = pd.read_csv(io.StringIO("\n".join(lines[table_start:])))
    if df.empty:
        raise ValueError("Giovanni returned no data for this area and time range.")
    time_col = df.columns[0]
    value_col = next((c for c in df.columns[1:] if c.lower().startswith("mean")), df.columns[1])
    times = pd.to_datetime(df[time_col], utc=True, errors="coerce").dt.tz_localize(None)
    values = pd.to_numeric(df[value_col], errors="coerce")
    series = pd.Series(values.values, index=times).dropna()
    fill = attrs.get("fill_value") or attrs.get("_FillValue")
    if fill:
        try:
            series = series[~np.isclose(series, float(fill))]
        except ValueError:
            pass
    series = series[series > -9000]  # common fill values like -9999.9
    return attrs, series.sort_index()


def unit_from_attrs(attrs: dict) -> str | None:
    for k, v in attrs.items():
        if "unit" in k.lower() and v:
            return v
    return None


# ---------------- NCSS CSV (NEX-GDDP-CMIP6) ----------------

_UNIT_RE = re.compile(r'\[unit="([^"]*)"\]')


def parse_ncss_point_csv(text: str) -> tuple[str | None, pd.Series]:
    """Parse a THREDDS NetCDF Subset Service point CSV.

    Header looks like: time,latitude[unit="degrees_north"],longitude[unit="degrees_east"],pr[unit="kg m-2 s-1"]
    """
    df = pd.read_csv(io.StringIO(text))
    if df.empty:
        raise ValueError("NCSS returned no rows")
    value_col = df.columns[-1]
    m = _UNIT_RE.search(value_col)
    unit = m.group(1) if m else None
    times = pd.to_datetime(df[df.columns[0]], utc=True, errors="coerce").dt.tz_localize(None)
    series = pd.Series(pd.to_numeric(df[value_col], errors="coerce").values, index=times).dropna()
    return unit, series.sort_index()


# ---------------- Aggregation ----------------


def aggregate(series: pd.Series, kind: str, native_step: str, frequency: str) -> pd.DataFrame:
    """Turn a base-unit series into the requested frequency.

    Returns a DataFrame with columns: value, complete (bool: whole period covered).
    flux values come in as mm/day rates and leave as totals (mm per month / year).
    """
    s = series.copy()

    # 1) bring sub-daily data to daily
    if native_step == "3H":
        s = s.resample("D").mean().dropna()

    if frequency == "daily":
        return pd.DataFrame({"value": s, "complete": True})

    # 2) monthly
    if native_step == "monthly":
        monthly = s.copy()
        monthly.index = monthly.index.to_period("M").to_timestamp()
        monthly = monthly.groupby(level=0).mean()
        if kind == "flux":
            monthly = monthly * monthly.index.days_in_month  # rate (mm/day) -> total mm
        m_complete = pd.Series(True, index=monthly.index)
    else:
        grouped = s.resample("MS")
        counts = grouped.count()
        monthly = grouped.sum() if kind == "flux" else grouped.mean()
        monthly = monthly[counts > 0]
        counts = counts[counts > 0]
        m_complete = counts >= monthly.index.days_in_month * 0.9
        if kind == "flux":  # scale up months with a few missing days
            monthly = monthly / counts * monthly.index.days_in_month

    if frequency == "monthly":
        return pd.DataFrame({"value": monthly, "complete": m_complete})

    # 3) yearly
    yg = monthly.groupby(monthly.index.year)
    yearly = yg.sum() if kind == "flux" else yg.mean()
    n_months = yg.count()
    yearly.index = pd.to_datetime(yearly.index.astype(str) + "-01-01")
    complete = pd.Series((n_months == 12).values, index=yearly.index)
    return pd.DataFrame({"value": yearly.values, "complete": complete.values}, index=yearly.index)


def unit_label(kind: str, frequency: str) -> str:
    if kind == "flux":
        return {"daily": "mm/day", "monthly": "mm/month", "yearly": "mm/year"}[frequency]
    if kind == "temp":
        return "°C"
    return "% water by volume"


def date_label(ts: pd.Timestamp, frequency: str) -> str:
    if frequency == "yearly":
        return ts.strftime("%Y")
    if frequency == "monthly":
        return ts.strftime("%Y-%m")
    return ts.strftime("%Y-%m-%d")


def summarize(df: pd.DataFrame, frequency: str) -> dict:
    """Plain numbers a farmer can read: average, highest, lowest, trend per decade."""
    use = df[df["complete"]] if df["complete"].any() else df
    vals = use["value"]
    if vals.empty:
        return {}
    hi_t, lo_t = vals.idxmax(), vals.idxmin()
    summary = {
        "average": _r(vals.mean()),
        "highest": {"date": date_label(hi_t, frequency), "value": _r(vals.max())},
        "lowest": {"date": date_label(lo_t, frequency), "value": _r(vals.min())},
        "trend_per_decade": None,
    }
    if len(vals) >= 3:
        years = (vals.index - vals.index[0]).days / 365.25
        if years[-1] >= 3:
            slope = np.polyfit(years, vals.values, 1)[0]
            summary["trend_per_decade"] = _r(slope * 10)
    return summary


def _r(x, nd=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), nd) + 0.0


def to_points(df: pd.DataFrame, frequency: str) -> list[dict]:
    return [
        {"date": date_label(t, frequency), "value": _r(v), "complete": bool(c)}
        for t, v, c in zip(df.index, df["value"], df["complete"])
    ]
