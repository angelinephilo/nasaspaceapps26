import math

import numpy as np
import pandas as pd

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _r(x, nd=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd) + 0.0


def _climatology(monthly: pd.Series) -> pd.Series | None:
    m = monthly.dropna()
    if m.empty:
        return None
    clim = m.groupby(m.index.month).mean()
    return clim if len(clim) == 12 else None


def _month_entry(clim: pd.Series, month: int) -> dict:
    return {"month": MONTH_NAMES[month - 1], "mean": _r(clim[month])}


def _high_low(monthly: pd.Series, high_key: str, low_key: str) -> dict | None:
    clim = _climatology(monthly)
    if clim is None:
        return None
    m = monthly.dropna()
    return {
        high_key: _month_entry(clim, int(clim.idxmax())),
        low_key: _month_entry(clim, int(clim.idxmin())),
        "seasonal_range": _r(clim.max() - clim.min()),
        "record_high": {"date": m.idxmax().strftime("%Y-%m"), "value": _r(m.max())},
        "record_low": {"date": m.idxmin().strftime("%Y-%m"), "value": _r(m.min())},
    }


def wettest_driest_months(monthly: pd.Series) -> dict | None:
    return _high_low(monthly, "wettest", "driest")


def hottest_coolest_months(monthly: pd.Series) -> dict | None:
    return _high_low(monthly, "hottest", "coolest")


def soil_moisture_extremes(monthly: pd.Series) -> dict | None:
    return _high_low(monthly, "highest", "lowest")


def evapotranspiration_extremes(monthly: pd.Series) -> dict | None:
    return _high_low(monthly, "highest", "lowest")


def anomaly_analysis(series: pd.Series, frequency: str, monthly: pd.Series | None = None, top: int = 6) -> dict | None:
    if frequency == "monthly" and monthly is not None:
        s = monthly.dropna()
        if s.empty:
            return None
        g = s.groupby(s.index.month)
        anomaly = s - g.transform("mean")
        z = anomaly / g.transform("std").replace(0, np.nan)
        fmt = "%Y-%m"
    elif frequency == "yearly":
        s = series.dropna()
        if len(s) < 3 or s.std() == 0:
            return None
        anomaly = s - s.mean()
        z = anomaly / s.std()
        fmt = "%Y"
    else:
        return None
    z = z.dropna()
    if z.empty:
        return None
    order = z.abs().sort_values(ascending=False).index[:top]
    return {
        "count_beyond_2sd": int((z.abs() >= 2).sum()),
        "unusual": [
            {"date": d.strftime(fmt), "value": _r(s[d]), "anomaly": _r(anomaly[d]), "z": _r(z[d])}
            for d in order
        ],
        "series": [{"date": d.strftime(fmt), "anomaly": _r(anomaly[d]), "z": _r(z[d])} for d in z.index],
    }


def seasonal_statistics(variable: str, series: pd.Series, monthly: pd.Series, frequency: str) -> dict:
    clim = _climatology(monthly) if monthly is not None else None
    out = {
        "monthly_means": [{"month": MONTH_NAMES[i - 1][:3], "mean": _r(clim[i])} for i in range(1, 13)] if clim is not None else None,
        "anomaly_analysis": anomaly_analysis(series, frequency, monthly),
    }
    if monthly is None:
        return out
    if variable == "precipitation":
        out["wettest_driest_months"] = wettest_driest_months(monthly)
    elif variable in ("air_temp", "min_temp", "max_temp"):
        out["hottest_coolest_months"] = hottest_coolest_months(monthly)
    elif variable == "soil_moisture":
        out["soil_moisture_extremes"] = soil_moisture_extremes(monthly)
    elif variable == "evapotranspiration":
        out["evapotranspiration_extremes"] = evapotranspiration_extremes(monthly)
    return out
