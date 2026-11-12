"""The MCP endpoint (app/mcp_server.py) for AI agents such as Claude Code:

    claude mcp add --transport http dbbutler https://<dbbutler>/api/mcp \\
        --header "Authorization: Bearer <MCP_TOKEN>"

MCP_TOKEN only (app/auth.py::require_mcp_token) — never AUTH_TOKEN — and
off (404) until that's set. POST only: no server-to-client stream, so GET
answers 405 as the Streamable HTTP transport specifies.
"""

import json

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.auth import require_mcp_token
from app.config import settings
from app.mcp_server import INVALID_REQUEST, PARSE_ERROR, handle

router = APIRouter(tags=["mcp"], dependencies=[Depends(require_mcp_token)])

MAX_BODY_BYTES = 1024 * 1024


def _allowed_origins() -> set[str]:
    return {o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()}


def _rpc_error(status: int, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}, status_code=status)


@router.post("/api/mcp")
async def mcp_post(request: Request):
    # DNS-rebinding guard the spec requires: a browser page on another
    # origin must not be able to drive the endpoint. Agents send no Origin.
    origin = request.headers.get("origin")
    if origin is not None and origin not in _allowed_origins():
        return _rpc_error(403, INVALID_REQUEST, "Origin not allowed.")
    # Refuse an announced oversized body before reading it; the length
    # check after reading covers a chunked one.
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return _rpc_error(413, INVALID_REQUEST, "Request body too large.")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _rpc_error(413, INVALID_REQUEST, "Request body too large.")
    try:
        message = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _rpc_error(400, PARSE_ERROR, "Parse error: the body is not valid JSON.")
    # Tools do blocking database reads: keep them off the event loop.
    status, payload = await run_in_threadpool(handle, message, request.headers)
    if payload is None:
        return Response(status_code=status)
    return JSONResponse(payload, status_code=status)


@router.get("/api/mcp")
@router.delete("/api/mcp")
def mcp_not_allowed():
    return Response(status_code=405, headers={"Allow": "POST"})
