import math

import numpy as np
import pandas as pd
from scipy import stats as st

ALPHA = 0.05
MIN_TREND_YEARS = 3


def _r(x, nd=3):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd) + 0.0


def _label(ts: pd.Timestamp, frequency: str) -> str:
    if frequency == "yearly":
        return ts.strftime("%Y")
    if frequency == "monthly":
        return ts.strftime("%Y-%m")
    return ts.strftime("%Y-%m-%d")


def _years_since_start(index: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray((index - index[0]).days / 365.25, dtype=float)


def _deseasonalize(series: pd.Series) -> pd.Series:
    return series - series.groupby(series.index.month).transform("mean")


def _trend_input(series: pd.Series, frequency: str) -> tuple[pd.Series, float] | None:
    s = series.dropna().sort_index()
    if len(s) < 5:
        return None
    if (s.index[-1] - s.index[0]).days / 365.25 < MIN_TREND_YEARS:
        return None
    if frequency == "monthly":
        return _deseasonalize(s), float(s.mean())
    if frequency == "daily":
        return None
    return s, 0.0


def regression_line(series: pd.Series, frequency: str = "yearly") -> dict | None:
    prepared = _trend_input(series, frequency)
    if prepared is None:
        return None
    s, level = prepared
    t = _years_since_start(s.index)
    lr = st.linregress(t, s.values.astype(float))
    fitted = lr.intercept + lr.slope * t + level
    return {
        "slope_per_decade": _r(lr.slope * 10),
        "intercept": _r(lr.intercept + level),
        "r2": _r(lr.rvalue ** 2),
        "p": _r(lr.pvalue, 4),
        "line": [{"date": _label(d, frequency), "value": _r(v, 3)} for d, v in zip(s.index, fitted)],
    }


def _change_in_mean(series: pd.Series, frequency: str) -> dict | None:
    s = series.dropna().sort_index()
    years = sorted(set(s.index.year))
    if len(years) < 2:
        return None
    mid = len(years) // 2
    a_years, b_years = years[:mid], years[mid:]
    a = s[s.index.year.isin(a_years)]
    b = s[s.index.year.isin(b_years)]
    change = b.mean() - a.mean()
    return {
        "period_a": {"years": [a_years[0], a_years[-1]], "mean": _r(a.mean(), 2), "n": int(len(a))},
        "period_b": {"years": [b_years[0], b_years[-1]], "mean": _r(b.mean(), 2), "n": int(len(b))},
        "change": _r(change, 2),
        "percent_change": _r(change / abs(a.mean()) * 100, 1) if a.mean() else None,
    }


def change_in_mean_temperature(series: pd.Series, frequency: str) -> dict | None:
    return _change_in_mean(series, frequency)


def change_in_mean_precipitation(series: pd.Series, frequency: str) -> dict | None:
    return _change_in_mean(series, frequency)


def change_in_mean_soil_moisture(series: pd.Series, frequency: str) -> dict | None:
    return _change_in_mean(series, frequency)


def change_in_mean_evapotranspiration(series: pd.Series, frequency: str) -> dict | None:
    return _change_in_mean(series, frequency)


def _mann_kendall(y: np.ndarray) -> tuple[float, float, float, float]:
    n = len(y)
    s = float(np.triu(np.sign(y[None, :] - y[:, None]), k=1).sum())
    _, counts = np.unique(y, return_counts=True)
    ties = counts[counts > 1]
    var_s = (n * (n - 1) * (2 * n + 5) - float(np.sum(ties * (ties - 1) * (2 * ties + 5)))) / 18.0
    if var_s <= 0:
        return s, 0.0, 1.0, 0.0
    z = (s - 1) / math.sqrt(var_s) if s > 0 else (s + 1) / math.sqrt(var_s) if s < 0 else 0.0
    p = 2 * (1 - st.norm.cdf(abs(z)))
    return s, z, p, s / (n * (n - 1) / 2)


def mann_kendall_sens_slope(series: pd.Series, frequency: str = "yearly") -> dict | None:
    prepared = _trend_input(series, frequency)
    if prepared is None:
        return None
    s, _ = prepared
    y = s.values.astype(float)
    t = _years_since_start(s.index)
    _, z, p, tau = _mann_kendall(y)
    sen = st.theilslopes(y, t, alpha=0.95)
    significant = p < ALPHA
    direction = "increasing" if sen.slope > 0 else "decreasing" if sen.slope < 0 else "no change"
    return {
        "significant": bool(significant),
        "direction": direction if significant else "no clear",
        "p": _r(p, 4),
        "z": _r(z),
        "tau": _r(tau),
        "sens_slope_per_decade": _r(sen.slope * 10),
        "sens_ci_per_decade": [_r(sen.low_slope * 10), _r(sen.high_slope * 10)],
        "n": int(len(y)),
        "deseasonalized": frequency == "monthly",
    }


CHANGE_FUNCTIONS = {
    "precipitation": change_in_mean_precipitation,
    "soil_moisture": change_in_mean_soil_moisture,
    "evapotranspiration": change_in_mean_evapotranspiration,
    "air_temp": change_in_mean_temperature,
    "min_temp": change_in_mean_temperature,
    "max_temp": change_in_mean_temperature,
}


def descriptive_statistics(variable: str, series: pd.Series, frequency: str) -> dict:
    change_fn = CHANGE_FUNCTIONS.get(variable, _change_in_mean)
    return {
        "change_in_mean": change_fn(series, frequency),
        "regression_line": regression_line(series, frequency),
        "mann_kendall_sens_slope": mann_kendall_sens_slope(series, frequency),
    }
