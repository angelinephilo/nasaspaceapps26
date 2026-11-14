import datetime as dt
import json
import tempfile
import xarray as xr
from harmony import Client, Collection, Request
from shapely.geometry import shape, mapping
from shapely.ops import orient
from datasets import DATASETS

# Reads EDL_USERNAME and EDL_PASSWORD from Cloud Run environment/secrets
harmony_client = Client()

def submit_subset_job(dataset_key: str, start_date: str, end_date: str, geojson_geometry: dict) -> str:
    meta = DATASETS[dataset_key]
    
    # Enforce counter-clockwise right-hand rule required by NASA Harmony
    poly = orient(shape(geojson_geometry), sign=1.0)
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": mapping(poly), "properties": {}}],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False) as tmp:
        json.dump(feature_collection, tmp)
        tmp_path = tmp.name

    req = Request(
        collection=Collection(id=meta["collection_id"]),
        variables=[meta["variable"]],
        temporal={
            "start": dt.datetime.fromisoformat(start_date),
            "stop": dt.datetime.fromisoformat(end_date),
        },
        shape=tmp_path,
        format="application/x-netcdf4",
    )
    return harmony_client.submit(req)

def check_job_status(job_id: str) -> dict:
    return harmony_client.status(job_id)

def extract_time_series(job_id: str, dataset_key: str) -> list[dict]:
    var_name = DATASETS[dataset_key]["variable"]
    with tempfile.TemporaryDirectory() as tmpdir:
        futures = harmony_client.download_all(job_id, directory=tmpdir)
        files = [f.result() for f in futures]
        
        ds = xr.open_mfdataset(files, combine="by_coords")
        # Spatial mean across the farmer's polygon
        spatial_dims = [d for d in ds[var_name].dims if d != "time"]
        ts = ds[var_name].mean(dim=spatial_dims, skipna=True)
        
        return [
            {"date": str(t)[:10], "value": round(float(v), 4)}
            for t, v in zip(ts["time"].values, ts.values)
        ]