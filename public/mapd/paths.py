from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = PROJECT_ROOT / "public"
LOCAL_ROOT = PROJECT_ROOT / "local"

LAYOUTS_ROOT = PUBLIC_ROOT / "layouts"
SCENARIOS_ROOT = PUBLIC_ROOT / "scenarios"
GIFS_ROOT = LOCAL_ROOT / "gifs"
RESULTS_ROOT = LOCAL_ROOT / "results"
DEBUGGING_ROOT = LOCAL_ROOT / "debugging"
