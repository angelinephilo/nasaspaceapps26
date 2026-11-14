import numpy as np
import pandas as pd
from scipy import stats as st


def regression_line(series: pd.Series) -> dict:
    ...


def change_in_mean_temperature(series: pd.Series, frequency: str) -> dict:
    ...


def change_in_mean_precipitation(series: pd.Series, frequency: str) -> dict:
    ...


def change_in_mean_soil_moisture(series: pd.Series, frequency: str) -> dict:
    ...


def change_in_mean_evapotranspiration(series: pd.Series, frequency: str) -> dict:
    ...


def mann_kendall_sens_slope(series: pd.Series) -> dict:
    ...


def descriptive_statistics(variable: str, series: pd.Series, frequency: str) -> dict:
    ...