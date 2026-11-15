"""Historical data: Giovanni Area-Averaged Time Series through NASA Harmony.

Follows NASA GES DISC's tutorial "How to Access the Giovanni Area-Averaged Time
Series ... Service Using Python" (nasa/gesdisc-tutorials): a harmony-py Request with
`average="area"` and `format="text/csv"` returns one CSV of area-averaged values.
"""
import datetime as dt
import os
import threading
import time
from collections import OrderedDict
from functools import lru_cache

import requests
from harmony import BBox, Client, Collection, Request

from datasets import HISTORICAL, pick_source
from descriptive_stats import descriptive_statistics
from seasonal_stats import seasonal_statistics
from processing import (
    aggregate, default_unit_for, pad_bbox, parse_giovanni_csv, summarize,
    to_base_units, to_points, unit_from_attrs, unit_label,
)

CMR_URL = "https://cmr.earthdata.nasa.gov/search/collections.json"

_client = None
_client_lock = threading.Lock()


def harmony_client() -> Client:
    """Created on first use so the app can start without credentials (e.g. for tests)."""
    global _client
    with _client_lock:
        if _client is None:
            # Keep only the first line and trim spaces: a pasted token often carries a stray
            # newline or a second setting, which breaks the HTTP Authorization header.
            token = (os.getenv("EDL_TOKEN") or "").strip().splitlines()[0:1]
            token = token[0].strip() if token else ""
            user = (os.getenv("EDL_USERNAME") or "").strip()
            pw = (os.getenv("EDL_PASSWORD") or "").strip()
            if token:
                _client = Client(token=token)
            elif user and pw:
                _client = Client(auth=(user, pw))
            else:
                _client = Client()  # falls back to ~/.netrc
        return _client


@lru_cache(maxsize=64)
def concept_id(short_name: str, version: str) -> str:
    """Look up the collection's CMR concept ID (e.g. GPM_3IMERGM v07 -> C2723754851-GES_DISC)."""
    r = requests.get(CMR_URL, params={"short_name": short_name, "version": version}, timeout=30)
    r.raise_for_status()
    entries = r.json()["feed"]["entry"]
    if not entries:
        raise RuntimeError(f"Collection {short_name} v{version} not found in NASA CMR")
    return entries[0]["id"]


def submit(variable: str, frequency: str, start: dt.date, end: dt.date, bbox) -> tuple[str, list]:
    """Submit an area-average job. Returns (harmony job id, padded bbox used)."""
    src = pick_source(variable, frequency)
    first = dt.date.fromisoformat(src.first_date)
    if end < first:
        raise ValueError(f"{HISTORICAL[variable]['label']} data starts in {first.year}.")
    start = max(start, first)
    box = pad_bbox(bbox, src.grid_deg)

    # Same request already sent recently (running or finished)? Reuse that NASA job instead
    # of queueing another one under the same Earthdata account.
    key = (src.giovanni_var, start.isoformat(), end.isoformat(), tuple(round(v, 3) for v in box))
    now = time.time()
    with _submitted_lock:
        for k, (_, t) in list(_submitted.items()):
            if now - t > REUSE_SECONDS:
                del _submitted[k]
        hit = _submitted.get(key)
        if hit and hit[0] not in _failed_jobs:
            return hit[0], list(box)

    req = Request(
        collection=Collection(id=concept_id(src.short_name, src.version)),
        spatial=BBox(*box),  # (west, south, east, north)
        temporal={
            "start": dt.datetime.combine(start, dt.time.min),
            "stop": dt.datetime.combine(end, dt.time(23, 59, 59)),
        },
        variables=[src.giovanni_var],
        format="text/csv",
        average="area",
    )
    job_id = harmony_client().submit(req)
    with _submitted_lock:
        _submitted[key] = (job_id, now)
    return job_id, list(box)


# request key -> (harmony job id, time submitted); lets identical requests share one NASA job
REUSE_SECONDS = 6 * 3600
_submitted: dict = {}
_submitted_lock = threading.Lock()
_failed_jobs: set = set()


# Finished results are small; keep the last ones so repeat polls don't re-download.
_results: "OrderedDict[str, dict]" = OrderedDict()
_results_lock = threading.Lock()

RUNNING_STATES = {"accepted", "running", "previewing", "paused", "running_with_errors"}
DONE_STATES = {"successful", "complete_with_errors"}


def job_status(job_id: str, variable: str, frequency: str) -> dict:
    with _results_lock:
        if job_id in _results:
            return _results[job_id]

    info = harmony_client().status(job_id)
    state = info["status"]
    if state not in DONE_STATES:
        out = {"status": "failed" if state not in RUNNING_STATES else "running",
               "progress": info.get("progress", 0), "message": info.get("message", "")}
        if state not in RUNNING_STATES:
            out["message"] = _friendly_error(info)
            _failed_jobs.add(job_id)  # don't hand this job to later identical requests
        return out

    result = build_result(job_id, variable, frequency)
    with _results_lock:
        _results[job_id] = result
        while len(_results) > 200:
            _results.popitem(last=False)
    return result


def build_result(job_id: str, variable: str, frequency: str) -> dict:
    client = harmony_client()
    urls = list(client.result_urls(job_id))
    csv_url = next((u for u in urls if u.lower().endswith(".csv")), urls[0] if urls else None)
    if not csv_url:
        return {"status": "failed", "progress": 100, "message": "NASA returned no files for this request."}
    text = client.read_text(csv_url)
    return process_csv(text, variable, frequency)


def process_csv(text: str, variable: str, frequency: str) -> dict:
    meta = HISTORICAL[variable]
    src = pick_source(variable, frequency)
    attrs, raw = parse_giovanni_csv(text)
    unit = unit_from_attrs(attrs)
    base = to_base_units(raw, unit, meta["kind"], default_unit_for(src.giovanni_var), meta.get("soil_layer_m", 0.1))
    df = aggregate(base, meta["kind"], src.step, frequency)
    monthly_df = df if frequency == "monthly" else aggregate(base, meta["kind"], src.step, "monthly")

    series = df.loc[df["complete"], "value"]
    monthly = monthly_df.loc[monthly_df["complete"], "value"]

    return {
        "status": "successful",
        "progress": 100,
        "variable": variable,
        "label": meta["label"],
        "frequency": frequency,
        "unit": unit_label(meta["kind"], frequency),
        "source": meta["about"],
        "giovanni_variable": src.giovanni_var,
        "data": to_points(df, frequency),
        "monthly": to_points(monthly_df, "monthly"),
        "summary": summarize(df, frequency),
        "descriptive_stats": descriptive_statistics(variable, series, frequency),
        "seasonal_stats": seasonal_statistics(variable, series, monthly, frequency),
    }


def _friendly_error(info: dict) -> str:
    msg = info.get("message") or ""
    errs = info.get("errors") or []
    detail = "; ".join(e.get("message", "") for e in errs if isinstance(e, dict))
    text = (detail or msg).strip()
    if "quota" in text.lower():
        return "The NASA Giovanni monthly usage limit has been reached. Please try again next month or use a shorter time range."
    return text or "NASA could not process this request. Try a shorter time range."
