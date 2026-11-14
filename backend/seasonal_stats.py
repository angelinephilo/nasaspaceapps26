import pandas as pd


def wettest_driest_months(monthly: pd.Series) -> dict:
    ...


def hottest_coolest_months(monthly: pd.Series) -> dict:
    ...


def soil_moisture_extremes(series: pd.Series, frequency: str) -> dict:
    ...


def evapotranspiration_extremes(series: pd.Series, frequency: str) -> dict:
    ...


def anomaly_analysis(series: pd.Series, frequency: str) -> dict:
    ...


def seasonal_statistics(variable: str, series: pd.Series, monthly: pd.Series, frequency: str) -> dict:
    ...