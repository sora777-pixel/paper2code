"""run_figures.py - runs figures/draw_<id>.py scripts. Each script defines main(results_dir, out_dir)."""
import argparse
import importlib.util
import os
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


def run_one(path: Path, results: Path, out: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(str(results), str(out))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=os.environ.get("P2C_ONLY", ""))
    ap.add_argument("--results", default=str(HERE.parent / "demo" / "results"))
    ap.add_argument("--out", default=str(HERE / "out"))
    a = ap.parse_args()
    status, rc = {}, 0
    for f in sorted(HERE.glob("draw_*.py")):
        fid = f.stem[len("draw_"):]
        if a.only and fid != a.only:
            continue
        try:
            run_one(f, Path(a.results), Path(a.out))
            status[fid] = "ok"
        except Exception:
            status[fid] = "error: " + traceback.format_exc(limit=6)
            print(f"[{fid}] FAILED\n{status[fid]}", file=sys.stderr)
            rc = 1
    if not a.only:
        (HERE / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    sys.exit(rc)
