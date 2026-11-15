import sys
from pathlib import Path

import xarray as xr


def compress(src_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(src_dir.glob("*.nc"))
    for i, f in enumerate(files, 1):
        out = out_dir / f.name
        if out.exists():
            continue
        with xr.open_dataset(f) as ds:
            ds = ds.load()
        enc = {v: {"zlib": True, "complevel": 5, "dtype": "int16", "scale_factor": 0.1, "_FillValue": -32768}
               for v in ds.data_vars}
        ds.to_netcdf(out, encoding=enc)
        print(f"[{i}/{len(files)}] {f.name}: {f.stat().st_size / 1e6:.1f} MB -> {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    compress(Path(sys.argv[1]), Path(sys.argv[2]))