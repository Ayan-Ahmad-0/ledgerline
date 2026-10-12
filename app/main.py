import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.sentry import init_sentry
from app.core.request_id import RequestIdMiddleware
from app.api.middleware import RequestMetricsMiddleware
from app.api.router import api_router

settings = get_settings()
setup_logging(level="DEBUG" if settings.DEBUG else "INFO")
init_sentry()

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Automated invoice/receipt extraction pipeline with mathematical validation and calibrated confidence routing",
    version="0.1.0",
)


@app.on_event("startup")
async def on_startup():
    from app.db.session import init_db
    await init_db()


# Register middlewares
app.add_middleware(RequestIdMiddleware)
app.add_middleware(RequestMetricsMiddleware)

# Mount static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Register API routers
app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    """Serve the local Ledgerline web UI dashboard."""
    index_file = Path(__file__).parent / "static" / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "project": settings.PROJECT_NAME,
        "status": "operational",
        "docs_url": "/docs",
    }
