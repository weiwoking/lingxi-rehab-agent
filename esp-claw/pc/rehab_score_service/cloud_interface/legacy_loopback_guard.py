"""Network boundary for legacy endpoints that accept local data or paths."""

import ipaddress
from typing import Iterable

from fastapi.responses import JSONResponse


LEGACY_WRITE_PATHS = frozenset(
    {
        "/score_trial",
        "/reset_fatigue_state",
        "/set_baseline_from_rows",
    }
)


def is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def install_legacy_loopback_guard(app, paths: Iterable[str] = LEGACY_WRITE_PATHS) -> None:
    protected = frozenset(paths)

    @app.middleware("http")
    async def legacy_loopback_guard(request, call_next):
        client_host = request.client.host if request.client else ""
        if (
            request.method.upper() == "POST"
            and request.url.path in protected
            and not is_loopback_host(client_host)
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error_code": "legacy_endpoint_loopback_only",
                    "message": "legacy scoring endpoints are available only from loopback",
                },
            )
        return await call_next(request)
