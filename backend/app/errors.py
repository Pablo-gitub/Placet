"""Stable product error codes with English diagnostics for API clients."""
from fastapi import HTTPException
from fastapi.responses import JSONResponse


class ProductError(HTTPException):
    def __init__(self, status_code: int, code: str, detail: str, params=None):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.params = params or {}


async def product_error_response(_request, exc: ProductError):
    return JSONResponse(status_code=exc.status_code, content={
        "code": exc.code, "params": exc.params, "detail": exc.detail,
    })
