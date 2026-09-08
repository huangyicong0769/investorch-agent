"""REST adapter: strict parsing, service calls and non-sensitive errors only."""

import json

from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .contracts import fields, invalid, sequence
from .domain import ExecutionError
from .service import ExecutionNodeService


def register_routes(server, service: ExecutionNodeService, security: TransportSecurityMiddleware):
    def route(path, methods):
        def register(handler):
            async def endpoint(request: Request):
                rejected = await security.validate_request(request)
                if rejected is not None:
                    return JSONResponse(
                        {"code": "INVALID_REQUEST", "message": "Host or Origin is not allowed.", "retryable": False},
                        status_code=rejected.status_code,
                    )
                try:
                    if request.query_params:
                        invalid("Query parameters are not supported.")
                    return JSONResponse(await handler(request))
                except ExecutionError as exc:
                    return JSONResponse(exc.to_wire(), status_code=exc.status)
                except Exception:
                    return JSONResponse(
                        {"code": "INTERNAL_ERROR", "message": "Execution operation failed.", "retryable": True},
                        status_code=500,
                    )

            server.custom_route(path, methods=methods)(endpoint)
            return endpoint

        return register

    @route("/api/v1/node/status", ["GET"])
    async def status(request):
        return service.get_node_status()

    @route("/api/v1/control-sessions", ["POST"])
    async def open_session(request):
        fields(await body(request, empty=True), set())
        return service.open_control_session()

    @route("/api/v1/control-sessions/{session_id}/renew", ["POST"])
    async def renew(request):
        value = await body(request, empty=True)
        if value:
            fields(value, {"reconciled_deployments"})
            if type(value["reconciled_deployments"]) is not list:
                invalid("reconciled_deployments must be an array.")
        return service.renew_control_session(request.path_params["session_id"], value.get("reconciled_deployments"))

    @route("/api/v1/control-sessions/{session_id}", ["DELETE"])
    async def close(request):
        fields(await body(request, empty=True), set())
        return service.close_control_session(request.path_params["session_id"])

    @route("/api/v1/deployments/{deployment_id}", ["PUT"])
    async def stage(request):
        return service.stage_deployment(request.path_params["deployment_id"], await body(request), authority(request))

    @route("/api/v1/facts/next", ["GET"])
    async def next_fact(request):
        return {"fact": service.get_next_pending_fact(authority(request))}

    @route("/api/v1/facts/{fact_id}/ack", ["POST"])
    async def ack(request):
        value = await body(request)
        fields(value, {"committed_ledger_sequence"})
        sequence(value["committed_ledger_sequence"])
        return service.ack_fact(request.path_params["fact_id"], value["committed_ledger_sequence"], authority(request))


def authority(request):
    values = request.headers.getlist("x-investorch-control-session")
    return values[0] if len(values) == 1 else None


async def body(request, *, empty=False):
    raw = await request.body()
    if not raw and empty:
        return {}

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                invalid("Duplicate JSON keys are not accepted.")
            result[key] = value
        return result

    try:
        result = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=lambda _: invalid("Nonfinite JSON is invalid.")
        )
    except (ValueError, UnicodeError, RecursionError):
        invalid("Request body must be valid JSON.")
    if type(result) is not dict:
        invalid("Request body must be a JSON object.")
    return result
