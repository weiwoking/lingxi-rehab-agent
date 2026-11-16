"""ASGI client-address policy for the persistent score Job API."""

import ipaddress

from fastapi.responses import JSONResponse


JOB_API_ROOT = "/api/v1/score/jobs"
JOB_HEALTH_PATH = "/api/v1/score/health"
RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def is_job_client_allowed(host: str) -> bool:
    """Allow only IPv4 loopback or RFC1918; unresolved and IPv6 hosts fail closed."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if not isinstance(address, ipaddress.IPv4Address):
        return False
    return address.is_loopback or any(address in network for network in RFC1918_NETWORKS)


def is_job_api_path(path: str) -> bool:
    return (
        path == JOB_HEALTH_PATH
        or path == JOB_API_ROOT
        or path.startswith(JOB_API_ROOT + "/")
    )


def install_job_client_network_policy(app) -> None:
    """Protect health and every persistent Job route using request.client.host."""

    @app.middleware("http")
    async def job_client_network_policy(request, call_next):
        client_host = request.client.host if request.client else ""
        if is_job_api_path(request.url.path) and not is_job_client_allowed(client_host):
            return JSONResponse(
                status_code=403,
                content={
                    "error_code": "job_client_network_forbidden",
                    "message": "score Job API requires loopback or RFC1918 IPv4",
                },
            )
        return await call_next(request)
