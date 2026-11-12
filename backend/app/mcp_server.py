"""DbButler's MCP server: JSON-RPC over one HTTP POST endpoint
(routers/mcp.py), tools only (app/mcp_tools.py), plain application/json
responses — never a stream, no sessions, nothing sent unprompted.

Both protocol eras are served on the same endpoint, as the 2026-07-28
specification allows a server to:

- Modern (2026-07-28, stateless): every request carries its version in
  params._meta["io.modelcontextprotocol/protocolVersion"], echoed by the
  MCP-Protocol-Version header, plus Mcp-Method (and Mcp-Name for
  tools/call) headers that must match the body. No handshake;
  server/discover describes the server.
- Legacy (2025-03-26 through 2025-11-25): an initialize handshake, then
  requests with an optional MCP-Protocol-Version header. Without a session
  id there is nothing to keep between requests.

handle() is pure apart from running the tool: (status, body) out, the HTTP
plumbing (auth, Origin, JSON parsing) lives in the router.
"""

import base64
import json
import logging
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.mcp_tools import TOOLS, TOOLS_BY_NAME, ToolError, scrub_identities, validate_arguments
from app.version import APP_VERSION

logger = logging.getLogger(__name__)

MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = (MODERN_VERSION, *LEGACY_VERSIONS)
META_VERSION = "io.modelcontextprotocol/protocolVersion"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022

SERVER_INFO = {"name": "dbbutler", "title": "DbButler", "version": APP_VERSION}
TOOLS_TTL_MS = 3_600_000
MAX_TEXT_CHARS = 200_000

INSTRUCTIONS = (
    "DbButler monitors PostgreSQL databases and keeps their history: health findings with suggested fixes, "
    "metrics, the heaviest queries, plan changes, lock incidents and weekly reports. All tools are read-only "
    "and none runs SQL you write. Start with list_targets, then get_target_overview for 'what is wrong with X'. "
    "Query text comes back with literal values masked."
)


def _json_default(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID | Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _error(request_id, code: int, message: str, data=None) -> dict:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _decode_header(value: str | None) -> str | None:
    """Mcp-Name values that aren't plain ASCII arrive as =?base64?…?=."""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return value


def _call_tool(params: dict, modern: bool) -> dict:
    name = params.get("name")
    tool = TOOLS_BY_NAME.get(name) if isinstance(name, str) else None
    if tool is None:
        raise LookupError(f"Unknown tool: {name}")
    try:
        data = tool.handler(validate_arguments(tool.input_schema, params.get("arguments")))
        # Round-trip to plain JSON types (datetimes, UUIDs) for structuredContent,
        # then drop any session/owner role names from the text.
        structured = scrub_identities(json.loads(json.dumps(data, default=_json_default)))
        text = json.dumps(structured, indent=1, ensure_ascii=False)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n… (truncated)"
        result = {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": False}
    except ToolError as exc:
        result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception:
        # The agent gets a generic message; the log gets the traceback.
        logger.exception("MCP tool %s failed", name)
        result = {"content": [{"type": "text", "text": f"{name} failed inside DbButler; see the backend log."}], "isError": True}
    if modern:
        result["resultType"] = "complete"
    return result


def _discover() -> dict:
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_VERSIONS),
        "capabilities": {"tools": {}},
        "instructions": INSTRUCTIONS,
        "ttlMs": TOOLS_TTL_MS,
        "cacheScope": "private",
        "_meta": {"io.modelcontextprotocol/serverInfo": SERVER_INFO},
    }


def _initialize(params: dict) -> dict:
    requested = params.get("protocolVersion")
    version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


def _tools_list(modern: bool) -> dict:
    result = {"tools": [tool.definition() for tool in TOOLS]}
    if modern:
        result.update({"resultType": "complete", "ttlMs": TOOLS_TTL_MS, "cacheScope": "private"})
    return result


def handle(message, headers: Mapping[str, str]) -> tuple[int, dict | None]:
    """One parsed JSON-RPC message in, (HTTP status, response body or
    None for 202) out. `headers` must be case-insensitive (Starlette's)."""
    if isinstance(message, list):
        return 400, _error(None, INVALID_REQUEST, "Batch requests are not supported.")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return 400, _error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message.")
    if "method" not in message:
        if "result" in message or "error" in message:
            return 202, None  # a response to something we never asked; accepted and ignored
        return 400, _error(message.get("id"), INVALID_REQUEST, "A request needs a method.")
    request_id = message.get("id")
    if "id" not in message:
        return 202, None  # a notification (notifications/initialized, cancelled, ...)
    method = message["method"]
    params = message.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return 400, _error(request_id, INVALID_REQUEST, "method must be a string and params an object.")

    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    meta_version = meta.get(META_VERSION)
    header_version = headers.get("mcp-protocol-version")

    if method == "initialize":
        modern = False
    elif meta_version is not None:
        if meta_version != MODERN_VERSION:
            return 400, _error(
                request_id,
                UNSUPPORTED_VERSION,
                "Unsupported protocol version",
                {"supported": list(SUPPORTED_VERSIONS), "requested": meta_version},
            )
        mismatch = None
        if header_version != meta_version:
            mismatch = f"MCP-Protocol-Version header '{header_version}' does not match body value '{meta_version}'"
        elif headers.get("mcp-method") != method:
            mismatch = f"Mcp-Method header '{headers.get('mcp-method')}' does not match body value '{method}'"
        elif method == "tools/call" and _decode_header(headers.get("mcp-name")) != params.get("name"):
            mismatch = f"Mcp-Name header '{headers.get('mcp-name')}' does not match body value '{params.get('name')}'"
        if mismatch:
            return 400, _error(request_id, HEADER_MISMATCH, f"Header mismatch: {mismatch}")
        modern = True
    else:
        if header_version == MODERN_VERSION:
            return 400, _error(
                request_id, HEADER_MISMATCH, f"Header mismatch: a {MODERN_VERSION} request must carry _meta.{META_VERSION}"
            )
        if header_version is not None and header_version not in LEGACY_VERSIONS:
            return 400, _error(
                request_id,
                UNSUPPORTED_VERSION,
                "Unsupported protocol version",
                {"supported": list(SUPPORTED_VERSIONS), "requested": header_version},
            )
        modern = False

    if method == "initialize":
        return 200, _result(request_id, _initialize(params))
    if method == "ping":
        return 200, _result(request_id, {"resultType": "complete"} if modern else {})
    if method == "server/discover":
        return 200, _result(request_id, _discover())
    if method == "tools/list":
        return 200, _result(request_id, _tools_list(modern))
    if method == "tools/call":
        try:
            return 200, _result(request_id, _call_tool(params, modern))
        except LookupError as exc:
            return 200, _error(request_id, INVALID_PARAMS, str(exc))
    return (404 if modern else 200), _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")
