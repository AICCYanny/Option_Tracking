from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from apps.api.app.config import settings
from apps.api.app.routers import health, alerts, metrics, metrics_nested, review, review_nested
from apps.api.app.services.poller import PollerService

app = FastAPI(title='UW Alerts API', version='0.1.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(health.router)
app.include_router(alerts.router)
app.include_router(metrics_nested.router)
app.include_router(metrics.router)
app.include_router(review.router)
app.include_router(review_nested.router)

# Init/End Poller
@app.on_event("startup")
def _startup():
    if settings.poller_enabled:
        app.state.poller = PollerService()
        app.state.poller.start()

@app.on_event("shutdown")
def _shutdown():
    poller = getattr(app.state, "poller", None)
    if poller:
        poller.stop()

@app.get("/poller/status")
def poller_status():
    running = getattr(getattr(app.state, "poller", None), "running", False)
    return {"running": bool(running)}

@app.get("/")
def root(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "service": "uw-alerts-api",
        "endpoints": [
            f"{base}/healthz",
            f"{base}/readyz",
            f"{base}/alerts?limit=100&order=asc",
            f"{base}/alerts/{{alert_id}}",
            # Suggested
            f"{base}/alerts/{{alert_id}}/metrics/greeks",
            f"{base}/alerts/{{alert_id}}/metrics/price",
            f"{base}/alerts/{{alert_id}}/metrics/buckets?limit=5",
            # Deprecated
            f"{base}/metrics/greeks/{{alert_id}}",
            f"{base}/metrics/price/{{alert_id}}",
            f"{base}/metrics/bucket?alert_id=...",
            f"{base}/reviews?biz_date=YYYY-MM-DD&decision=accept|reject|watch",
            f"{base}/alerts/{{alert_id}}/review",
        ],
    }