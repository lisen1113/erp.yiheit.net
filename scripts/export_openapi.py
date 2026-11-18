"""Export deterministic OpenAPI from the application, without running a server."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from yihe_api.main import app  # noqa: E402

output = ROOT / "packages/contracts/openapi.json"
output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
