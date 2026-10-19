"""Protect every public HTTP route, including vLLM's non-/v1 auxiliary routes."""

import os
import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class InferenceAuth:
    def __init__(self, app):
        self.app = app
        key = os.environ.get("VLLM_API_KEY", "")
        if len(key) != 43:
            raise ValueError("Deployment-specific inference credential required")
        self.expected = ("Bearer " + key).encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            header = Headers(scope=scope).get("authorization", "").encode()
            if not secrets.compare_digest(header, self.expected):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse(
                        {"error": "Unauthorized"},
                        status_code=401,
                        headers={"Cache-Control": "no-store"},
                    )(scope, receive, send)
                return
        await self.app(scope, receive, send)
