import pandas as pd
from scipy import stats as st


def mann_whitney_u(series: pd.Series, period_a: tuple[int, int], period_b: tuple[int, int]) -> dict:
    ...