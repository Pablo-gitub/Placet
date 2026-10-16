"""Bound JSON message bodies before parsing base64 phone attachments."""
from starlette.responses import JSONResponse

MAX_MESSAGE_BODY = 29 * 1024 * 1024  # 20 MiB of bytes + base64 + message metadata


class MessageBodyLimit:
    def __init__(self, app, limit=MAX_MESSAGE_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or scope.get("method") != "POST" or not (path.startswith("/api/sessions/") and path.endswith("/messages")):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                response = JSONResponse({"detail": "Messaggio troppo grande: massimo 20 MB complessivi di allegati."}, status_code=413)
                return await response(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break
        async def buffered():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope, buffered, send)
