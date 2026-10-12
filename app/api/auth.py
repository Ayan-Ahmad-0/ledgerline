from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from app.core.config import get_settings

api_key_header = APIKeyHeader(name=get_settings().API_KEY_HEADER, auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Verify that the request contains a valid API key."""
    settings = get_settings()

    if not api_key or api_key not in settings.API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key in X-API-Key header",
        )
    return api_key
