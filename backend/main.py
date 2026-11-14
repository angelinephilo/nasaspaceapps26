from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from harmony_service import submit_subset_job, check_job_status, extract_time_series
from datasets import DATASETS

app = FastAPI()

class SubsetPayload(BaseModel):
    dataset_key: str
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    geometry: dict   # GeoJSON Polygon geometry

@app.get("/api/datasets")
def list_datasets():
    return {k: v["label"] for k, v in DATASETS.items()}

@app.post("/api/submit", status_code=202)
def create_job(payload: SubsetPayload):
    if payload.dataset_key not in DATASETS:
        raise HTTPException(status_code=400, detail="Unknown dataset")
    job_id = submit_subset_job(
        payload.dataset_key, payload.start_date, payload.end_date, payload.geometry
    )
    return {"job_id": job_id}

@app.get("/api/status/{job_id}")
def get_status(job_id: str, dataset_key: str):
    info = check_job_status(job_id)
    state = info.get("status")
    if state == "successful":
        series = extract_time_series(job_id, dataset_key)
        return {"status": state, "progress": 100, "data": series}
    return {"status": state, "progress": info.get("progress", 0)}