from __future__ import annotations

from . import dashboard as base

# Keep the proven V3.1 dashboard/auth implementation, but request the complete
# retained signal window so a busy scan cannot hide a low-scoring benchmark.
base.DASHBOARD_HTML = base.DASHBOARD_HTML.replace(
    "/api/signals?limit=100",
    "/api/signals?limit=1000",
)
base.DASHBOARD_HTML = base.DASHBOARD_HTML.replace(
    "<title>Ichimoku V3</title>",
    "<title>Ichimoku V3.2</title>",
).replace(
    "<h1>Ichimoku Scanner V3</h1>",
    "<h1>Ichimoku Scanner V3.2</h1>",
)
base.app.version = "3.2.0"

app = base.app
