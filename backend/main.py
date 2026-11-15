"""

POST /api/historical            submit a Giovanni (Harmony) area-average job -> {job}
GET  /api/historical/{job}      poll; returns the chart data once finished
POST /api/future                NEX-GDDP-CMIP6 projection (answers directly, can take ~30-90 s)
GET  /api/options               variables, frequencies, scenarios for the frontend
"""
import base64
import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Literal
import pandas as pd
import re

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import future_service
import giovanni_service
from datasets import (
    FUTURE_FIRST_YEAR, FUTURE_LAST_YEAR, FUTURE_VARIABLES, HISTORICAL,
    MAX_DAILY_RANGE_DAYS, SSP_SCENARIOS, pick_source,
)
from processing import bbox_center, bbox_from_geometry

log = logging.getLogger("agriclimate")
app = FastAPI(title="AgriClimate Explorer API")

origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"])


# ---------------- Request models ----------------

class Geometry(BaseModel):
    type: Literal["Polygon", "Point"]
    coordinates: list

    @field_validator("coordinates")
    @classmethod
    def check_coords(cls, v, info):
        try:
            pts = [v] if isinstance(v[0], (int, float)) else v[0]
            for lng, lat, *_ in pts:
                if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                    raise ValueError
        except Exception:
            raise ValueError("coordinates must be [lng, lat] pairs in GeoJSON order")
        return v


class HistoricalRequest(BaseModel):
    variable: str
    frequency: Literal["daily", "monthly", "yearly"]
    start: str = Field(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")
    end: str = Field(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM")
    geometry: Geometry


class FutureRequest(BaseModel):
    variable: str
    start_year: int
    end_year: int
    geometry: Geometry

# ---------------- Helpers ----------------

def month_range(start: str, end: str) -> tuple[dt.date, dt.date]:
    s = dt.date.fromisoformat(start + "-01")
    e_first = dt.date.fromisoformat(end + "-01")
    next_month = (e_first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    e = min(next_month - dt.timedelta(days=1), dt.date.today())
    if e < s:
        raise HTTPException(400, "The end month must be after the start month.")
    return s, e


def encode_job(job_id: str, variable: str, frequency: str) -> str:
    raw = json.dumps({"j": job_id, "v": variable, "f": frequency}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_job(token: str) -> tuple[str, str, str]:
    try:
        d = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        if d["v"] not in HISTORICAL:
            raise ValueError
        return d["j"], d["v"], d["f"]
    except Exception:
        raise HTTPException(400, "Unknown job.")


# ---------------- Endpoints ----------------

_SECRET_RE = re.compile(r"(Bearer\s+|token=|password=)[^\s'\"&]+", re.IGNORECASE)


def safe_error(e: Exception) -> str:
    return _SECRET_RE.sub(r"\1[hidden]", str(e))[:300]

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/options")
def options():
    return {
        "historical": {
            k: {
                "label": v["label"],
                "about": v["about"],
                "first_year": min(int(s.first_date[:4]) for s in v["sources"].values()),
            }
            for k, v in HISTORICAL.items()
        },
        "frequencies": ["monthly", "yearly", "daily"],
        "max_daily_range_days": MAX_DAILY_RANGE_DAYS,
        "future": {k: v["label"] for k, v in FUTURE_VARIABLES.items()},
        "ssp": SSP_SCENARIOS,
        "future_years": [FUTURE_FIRST_YEAR, FUTURE_LAST_YEAR],
    }


@app.post("/api/historical", status_code=202)
def submit_historical(req: HistoricalRequest):
    if req.variable not in HISTORICAL:
        raise HTTPException(400, "Unknown variable.")
    start, end = month_range(req.start, req.end)
    if req.frequency == "daily" and (end - start).days > MAX_DAILY_RANGE_DAYS:
        raise HTTPException(400, "Daily data is limited to 2 years. Choose Monthly or Yearly for longer periods.")
    try:
        job_id, box = giovanni_service.submit(req.variable, req.frequency, start, end, bbox_from_geometry(req.geometry.model_dump()))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("Harmony submit failed")
        raise HTTPException(502, f"Could not reach NASA Harmony: {e}")
    src = pick_source(req.variable, req.frequency)
    return {"job": encode_job(job_id, req.variable, req.frequency), "bbox": box, "giovanni_variable": src.giovanni_var}


@app.get("/api/historical/{job}")
def poll_historical(job: str):
    job_id, variable, frequency = decode_job(job)
    try:
        return giovanni_service.job_status(job_id, variable, frequency)
    except Exception as e:
        log.exception("Harmony status failed")
        raise HTTPException(502, f"Problem reading NASA results: {e}")


@app.post("/api/future")
def future(req: FutureRequest):
    if req.variable not in FUTURE_VARIABLES:
        raise HTTPException(400, "Unknown variable.")
    s, e = max(req.start_year, FUTURE_FIRST_YEAR), min(req.end_year, FUTURE_LAST_YEAR)
    if e < s:
        raise HTTPException(400, f"Choose years between {FUTURE_FIRST_YEAR} and {FUTURE_LAST_YEAR}.")
    if e - s > 50:
        raise HTTPException(400, "Choose a period of 50 years or less.")
    lat, lng = bbox_center(bbox_from_geometry(req.geometry.model_dump()))
    try:
        return future_service.projection_pair(req.variable, s, e, lat, lng)
    except ValueError as ex:
        raise HTTPException(400, safe_error(ex))
    except Exception as ex:
        log.exception("Future projection failed")
        raise HTTPException(502, safe_error(ex))

    
# Serve the frontend too when running locally (uvicorn main:app). On Firebase Hosting
# the frontend is served by Firebase and only /api/** reaches this service.
_frontend = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parent.parent / "frontend"))
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")