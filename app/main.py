from fastapi import FastAPI
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

# Register middlewares
app.add_middleware(RequestIdMiddleware)
app.add_middleware(RequestMetricsMiddleware)

# Register routers
app.include_router(api_router)


@app.get("/")
async def root():
    return {
        "project": settings.PROJECT_NAME,
        "status": "operational",
        "docs_url": "/docs",
    }
