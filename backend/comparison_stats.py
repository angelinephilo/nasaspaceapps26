import math

import pandas as pd
from scipy import stats as st

ALPHA = 0.05


def _r(x, nd=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd) + 0.0


def mann_whitney_u(series: pd.Series, period_a: tuple[int, int], period_b: tuple[int, int]) -> dict:
    s = series.dropna()
    a = s[(s.index.year >= period_a[0]) & (s.index.year <= period_a[1])]
    b = s[(s.index.year >= period_b[0]) & (s.index.year <= period_b[1])]
    if len(a) < 3 or len(b) < 3:
        raise ValueError("Each period needs at least 3 values to compare.")
    res = st.mannwhitneyu(b.values, a.values, alternative="two-sided")
    diff = b.mean() - a.mean()
    return {
        "test": "Mann-Whitney U (two-sided)",
        "period_a": {"years": list(period_a), "n": int(len(a)), "mean": _r(a.mean()), "median": _r(a.median())},
        "period_b": {"years": list(period_b), "n": int(len(b)), "mean": _r(b.mean()), "median": _r(b.median())},
        "difference": _r(diff),
        "percent_change": _r(diff / abs(a.mean()) * 100, 1) if a.mean() else None,
        "u": _r(res.statistic, 1),
        "p": _r(res.pvalue, 4),
        "effect_size": _r(2 * res.statistic / (len(a) * len(b)) - 1, 3),
        "significant": bool(res.pvalue < ALPHA),
    }

def compare_scenarios(a: pd.Series, b: pd.Series, a_name: str, b_name: str) -> dict:
    a, b = a.dropna(), b.dropna()
    if len(a) < 3 or len(b) < 3:
        raise ValueError("Each scenario needs at least 3 years to compare. Choose a longer period.")
    res = st.mannwhitneyu(b.values, a.values, alternative="two-sided")
    diff = b.mean() - a.mean()
    return {
        "test": "Mann-Whitney U (two-sided)",
        "scenario_a": {"name": a_name, "n": int(len(a)), "mean": _r(a.mean()), "median": _r(a.median())},
        "scenario_b": {"name": b_name, "n": int(len(b)), "mean": _r(b.mean()), "median": _r(b.median())},
        "difference": _r(diff),
        "percent_change": _r(diff / abs(a.mean()) * 100, 1) if a.mean() else None,
        "u": _r(res.statistic, 1),
        "p": _r(res.pvalue, 4),
        "effect_size": _r(2 * res.statistic / (len(a) * len(b)) - 1, 3),
        "significant": bool(res.pvalue < ALPHA),
    }