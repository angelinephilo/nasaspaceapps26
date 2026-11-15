"""Offline tests: no NASA credentials or network needed.  Run: cd backend && pytest -q"""
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import giovanni_service  # noqa: E402
import main  # noqa: E402
from processing import (  # noqa: E402
    aggregate, pad_bbox, parse_giovanni_csv, parse_ncss_point_csv, to_base_units,
)

GIOVANNI_MONTHLY_RAIN = """Title:,Area-averaged time series (Region: 103.7E, 1.3N, 103.8E, 1.4N)
User Start Date:,2023-01-01T00:00:00Z
User End Date:,2024-12-31T23:59:59Z
Bounding Box:,"103.7,1.3,103.8,1.4"
URL to Reproduce Results:,https://example
Fill Value (mean_GPM_3IMERGM_07_precipitation):,-9999.9
,
time,mean_GPM_3IMERGM_07_precipitation
""" + "\n".join(
    f"2023-{m:02d}-01 00:00:00,0.25" for m in range(1, 13)
) + "\n" + "\n".join(f"2024-{m:02d}-01 00:00:00,0.5" for m in range(1, 13)) + "\n2025-01-01 00:00:00,-9999.9\n"

NCSS_CSV = 'time,latitude[unit="degrees_north"],longitude[unit="degrees_east"],tasmax[unit="K"]\n' + "\n".join(
    f"2030-01-{d:02d}T12:00:00Z,1.375,103.875,{303.15}" for d in range(1, 32)
)


def test_pad_bbox_widens_small_farm():
    box = pad_bbox((103.80, 1.30, 103.81, 1.31), (0.25, 0.25))
    assert box[2] - box[0] == pytest.approx(0.25)
    assert box[3] - box[1] == pytest.approx(0.25)


def test_parse_giovanni_csv_and_fill_value():
    attrs, s = parse_giovanni_csv(GIOVANNI_MONTHLY_RAIN)
    assert len(s) == 24  # fill value row dropped
    assert s.iloc[0] == 0.25


def test_monthly_rain_mm_per_hour_to_totals():
    _, s = parse_giovanni_csv(GIOVANNI_MONTHLY_RAIN)
    base = to_base_units(s, None, "flux", "mm/hr")  # mm/hr -> mm/day
    monthly = aggregate(base, "flux", "monthly", "monthly")
    assert monthly["value"].iloc[0] == pytest.approx(0.25 * 24 * 31)  # January total
    yearly = aggregate(base, "flux", "monthly", "yearly")
    assert yearly["value"].iloc[0] == pytest.approx(0.25 * 24 * 365)
    assert yearly["complete"].all()


def test_temperature_and_soil_conversions():
    s = pd.Series([300.15])
    assert to_base_units(s, "K", "temp", "K").iloc[0] == pytest.approx(27.0)
    # 30 kg/m² of water in the top 10 cm = 30 % by volume
    assert to_base_units(pd.Series([30.0]), "kg m-2", "soil", "kg m-2", 0.1).iloc[0] == pytest.approx(30.0)
    assert to_base_units(pd.Series([1e-4]), "kg m-2 s-1", "flux", "").iloc[0] == pytest.approx(8.64)


def test_3hourly_to_daily():
    idx = pd.date_range("2024-01-01", periods=16, freq="3h")
    s = pd.Series(range(16), index=idx, dtype=float)
    daily = aggregate(s, "soil", "3H", "daily")
    assert list(daily["value"]) == [3.5, 11.5]


def test_parse_ncss():
    unit, s = parse_ncss_point_csv(NCSS_CSV)
    assert unit == "K" and len(s) == 31


def test_api_historical_flow(monkeypatch):
    calls = {}

    def fake_submit(variable, frequency, start, end, bbox):
        calls["args"] = (variable, frequency, start, end, bbox)
        return "job-123", [103.7, 1.3, 103.8, 1.4]

    monkeypatch.setattr(giovanni_service, "submit", fake_submit)
    monkeypatch.setattr(
        giovanni_service, "job_status",
        lambda j, v, f: giovanni_service.process_csv(GIOVANNI_MONTHLY_RAIN, v, f) if j == "job-123" else None,
    )
    client = TestClient(main.app)
    farm = {"type": "Polygon", "coordinates": [[[103.80, 1.30], [103.81, 1.30], [103.81, 1.31], [103.80, 1.30]]]}
    r = client.post("/api/historical", json={"variable": "precipitation", "frequency": "yearly",
                                             "start": "2023-01", "end": "2024-12", "geometry": farm})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    r2 = client.get(f"/api/historical/{job}")
    body = r2.json()
    assert body["status"] == "successful"
    assert body["unit"] == "mm/year"
    assert [p["date"] for p in body["data"]] == ["2023", "2024"]


def test_api_rejects_long_daily_range():
    client = TestClient(main.app)
    pt = {"type": "Point", "coordinates": [103.8, 1.35]}
    r = client.post("/api/historical", json={"variable": "precipitation", "frequency": "daily",
                                             "start": "2015-01", "end": "2024-12", "geometry": pt})
    assert r.status_code == 400


# ---------------- Statistics ----------------
import numpy as np  # noqa: E402

import comparison_stats  # noqa: E402
import descriptive_stats  # noqa: E402
import seasonal_stats  # noqa: E402


def _yearly(values, start=2000):
    return pd.Series(values, index=pd.to_datetime([f"{start + i}-01-01" for i in range(len(values))]), dtype=float)


def test_mann_kendall_sens_slope_known_trend():
    rng = np.random.default_rng(0)
    s = _yearly(20 + 0.05 * np.arange(25) + rng.normal(0, 0.1, 25))
    t = descriptive_stats.mann_kendall_sens_slope(s, "yearly")
    assert t["significant"] and t["direction"] == "increasing"
    assert t["sens_slope_per_decade"] == pytest.approx(0.5, abs=0.1)
    flat = descriptive_stats.mann_kendall_sens_slope(_yearly([20, 21, 19, 22, 18] * 5), "yearly")
    assert not flat["significant"]


def test_mann_kendall_matches_pymannkendall():
    pmk = pytest.importorskip("pymannkendall")
    rng = np.random.default_rng(3)
    y = np.round(rng.normal(0, 1, 40) + 0.03 * np.arange(40), 1)
    ours = descriptive_stats.mann_kendall_sens_slope(_yearly(y, 1980), "yearly")
    ref = pmk.original_test(y)
    assert ours["p"] == pytest.approx(ref.p, abs=1e-4)
    assert ours["tau"] == pytest.approx(ref.Tau, abs=1e-3)


def test_regression_line_and_change_in_mean():
    s = _yearly([10, 11, 12, 13, 14, 15, 16, 17])
    reg = descriptive_stats.regression_line(s, "yearly")
    assert reg["slope_per_decade"] == pytest.approx(10, abs=0.05) and reg["r2"] == pytest.approx(1.0)
    assert reg["line"][0]["date"] == "2000"
    ch = descriptive_stats.change_in_mean_precipitation(s, "yearly")
    assert ch["period_a"]["years"] == [2000, 2003] and ch["change"] == pytest.approx(4.0)


def test_monthly_trend_removes_seasonal_cycle():
    idx = pd.date_range("2005-01-01", "2024-12-01", freq="MS")
    s = pd.Series([10 * np.sin(d.month / 12 * 2 * np.pi) + 25 for d in idx], index=idx)
    t = descriptive_stats.mann_kendall_sens_slope(s, "monthly")
    assert not t["significant"] and t["sens_slope_per_decade"] == pytest.approx(0, abs=1e-6)


def test_seasonal_calendar_months_and_anomalies():
    idx = pd.date_range("2010-01-01", "2019-12-01", freq="MS")
    rain = pd.Series([300 if d.month == 12 else 50 if d.month == 6 else 150 for d in idx], index=idx, dtype=float)
    rain["2015-12-01"] = 600
    out = seasonal_stats.seasonal_statistics("precipitation", rain, rain, "monthly")
    assert out["wettest_driest_months"]["wettest"]["month"] == "December"
    assert out["wettest_driest_months"]["driest"]["month"] == "June"
    assert len(out["monthly_means"]) == 12
    assert out["anomaly_analysis"]["unusual"][0]["date"] == "2015-12"
    temp = seasonal_stats.seasonal_statistics("max_temp", rain, rain, "monthly")
    assert "hottest_coolest_months" in temp and "wettest_driest_months" not in temp


def test_mann_whitney_u_two_years():
    idx = pd.date_range("2010-01-01", periods=12, freq="MS").append(pd.date_range("2020-01-01", periods=12, freq="MS"))
    s = pd.Series(list(range(12)) + list(range(20, 32)), index=idx, dtype=float)
    res = comparison_stats.mann_whitney_u(s, (2010, 2010), (2020, 2020))
    assert res["significant"] and res["effect_size"] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        comparison_stats.mann_whitney_u(s, (2010, 2010), (2030, 2030))


def test_compare_scenarios():
    a = _yearly([31.0 + 0.01 * i for i in range(20)], 2026)
    b = _yearly([31.5 + 0.04 * i for i in range(20)], 2026)
    res = comparison_stats.compare_scenarios(a, b, "SSP1-2.6", "SSP5-8.5")
    assert res["significant"] and res["difference"] > 0
    assert res["scenario_a"]["n"] == 20


def _write_stats(dirpath):
    import xarray as xr
    lat = np.arange(-11.875, 38, 0.25)[:8] + 13
    lon = np.arange(100.125, 102.125, 0.25)
    def ds(t):
        shape2 = (len(lat), len(lon))
        data = {v: (("lat", "lon"), np.full(shape2, val)) for v, val in
                [("pr_annual", 2000 + t * 100), ("tasmin_annual", 24 + t), ("tasmax_annual", 32 + t),
                 ("rx1day", 80 + t), ("hotdays35", 5 + 10 * t)]}
        for v, val in [("pr_monthly", 150 + t), ("tasmin_monthly", 24 + t), ("tasmax_monthly", 32 + t)]:
            data[v] = (("month", "lat", "lon"), np.full((12,) + shape2, float(val)))
        return xr.Dataset(data, coords={"lat": lat, "lon": lon, "month": np.arange(1, 13)})
    ds(0).to_netcdf(dirpath / "baseline.nc")
    for i, p in enumerate(["2026-2040", "2041-2060"], 1):
        ds(i * 0.5).to_netcdf(dirpath / f"ssp126_{p}.nc")
        ds(i * 1.5).to_netcdf(dirpath / f"ssp585_{p}.nc")


def test_projections_api_point_and_area(tmp_path, monkeypatch):
    import climate_projections_api as cpa
    _write_stats(tmp_path)
    monkeypatch.setattr(cpa, "STATS_DIR", tmp_path)
    monkeypatch.setattr(cpa, "_DS", None)
    client = TestClient(main.app)
    r = client.get("/api/projections/point", params={"lat": 1.6, "lon": 100.6})
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["baseline"]["tasmax_annual"] == 32.0
    assert body["projections"]["ssp585_2041-2060"]["tasmax_annual"] == 35.0
    poly = {"type": "Polygon", "coordinates": [[[100.1, 1.2], [101.0, 1.2], [101.0, 2.0], [100.1, 2.0], [100.1, 1.2]]]}
    r = client.post("/api/projections/area", json={"geometry": poly})
    assert r.status_code == 200 and r.json()["mode"] == "area" and r.json()["baseline"]["n_cells"] > 1
    assert client.get("/api/projections/point", params={"lat": 50, "lon": 100}).status_code == 400


def test_future_tests_unavailable_when_nasa_fails(monkeypatch, tmp_path):
    import local_cmip6
    import nasa_fallback

    def boom(*a, **k):
        raise ConnectionError("NCCS unreachable")
    monkeypatch.setattr(local_cmip6, "LOCAL_DIR", tmp_path)
    monkeypatch.setattr(nasa_fallback._session, "get", boom)
    nasa_fallback.year_files.cache_clear()
    nasa_fallback.year_stats.cache_clear()
    client = TestClient(main.app)
    r = client.post("/api/future/tests", json={"variable": "max_temp", "start_year": 2026, "end_year": 2040,
                                               "geometry": {"type": "Point", "coordinates": [103.8, 1.35]}})
    assert r.status_code == 200 and r.json()["available"] is False and "NCCS" in r.json()["reason"]


def test_process_csv_has_stats_fields():
    res = giovanni_service.process_csv(GIOVANNI_MONTHLY_RAIN, "precipitation", "monthly")
    assert {"descriptive_stats", "seasonal_stats", "monthly"} <= set(res)


class _FakeResp:
    def __init__(self, text=None, content=None):
        self.text, self.content = text, content if content is not None else (text or "").encode()

    def raise_for_status(self):
        pass


def _fake_nccs_get(url, params=None, timeout=None):
    if url.endswith("catalog.xml"):
        exp, var = url.split("/")[-4], url.split("/")[-2]
        items = "".join(f'<dataset name="x" urlPath="AMES/NEX/GDDP-CMIP6/GFDL-ESM4/{exp}/r1i1p1f1/{var}/{var}_day_GFDL-ESM4_{exp}_r1i1p1f1_gr1_{y}.nc"/>'
                        for y in range(1990, 2101))
        return _FakeResp(content=f'<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"><dataset name="d">{items}</dataset></catalog>'.encode())
    var = params["var"]
    year = int(params["time_start"][:4])
    warm = 0.0 if "historical" in url else (1.0 if "ssp126" in url else 3.0) * (year - 2015) / 85
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    days = days[~((days.month == 2) & (days.day == 29))]
    if var == "pr":
        vals = [6.0 / 86400 * (1 + warm / 10)] * len(days)
        unit = "kg m-2 s-1"
    else:
        vals = [273.15 + (34.0 if var == "tasmax" else 24.0) + warm + (2 if d.month == 4 else 0) for d in days]
        unit = "K"
    rows = "\n".join(f"{d.strftime('%Y-%m-%d')}T12:00:00Z,{params['latitude']},{params['longitude']},{v}" for d, v in zip(days, vals))
    return _FakeResp(text=f'time,latitude[unit="degrees_north"],longitude[unit="degrees_east"],{var}[unit="{unit}"]\n{rows}')


def test_nasa_fallback_summaries_and_tests(monkeypatch, tmp_path):
    import local_cmip6
    import nasa_fallback
    monkeypatch.setattr(nasa_fallback._session, "get", _fake_nccs_get)
    nasa_fallback.year_files.cache_clear()
    nasa_fallback.year_stats.cache_clear()
    nasa_fallback._cache.clear()
    monkeypatch.setattr(local_cmip6, "LOCAL_DIR", tmp_path)
    client = TestClient(main.app)
    kenya = {"type": "Point", "coordinates": [36.8, -1.3]}
    r = client.post("/api/future/nasa", json={"variable": "max_temp", "geometry": kenya})
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["mode"] == "nasa" and body["lat"] == -1.375 and body["lon"] == 36.875
    assert body["baseline"]["tasmax_annual"] == pytest.approx(34.2, abs=0.05)
    assert body["baseline"]["tasmax_monthly"][3] == pytest.approx(36.0, abs=0.05)
    assert body["baseline"]["hotdays35"] == 30
    assert body["projections"]["ssp585_2081-2100"]["tasmax_annual"] > body["projections"]["ssp126_2081-2100"]["tasmax_annual"]
    rain = client.post("/api/future/nasa", json={"variable": "precipitation", "geometry": kenya}).json()
    assert rain["baseline"]["pr_annual"] == pytest.approx(6.0 * 365, rel=0.01) and "rx1day" in rain["baseline"]
    t = client.post("/api/future/tests", json={"variable": "max_temp", "start_year": 2041, "end_year": 2060, "geometry": kenya}).json()
    assert t["available"] and t["source"] == "nasa" and t["comparison"]["significant"]
    assert t["trends"]["ssp585"]["mann_kendall_sens_slope"]["direction"] == "increasing"
