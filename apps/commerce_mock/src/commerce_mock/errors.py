"""One error body for every failure: dtc_contracts.commerce.ErrorOut."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from dtc_contracts.commerce import ErrorCode, ErrorOut


class ApiError(Exception):
    def __init__(self, status_code: int, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code: ErrorCode = code
        self.detail = detail


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def _body(code: ErrorCode, detail: str) -> dict[str, str]:
    return ErrorOut(code=code, detail=detail).model_dump()


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(_body(exc.code, exc.detail), status_code=exc.status_code)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return JSONResponse(_body("invalid_request", str(exc.errors())), status_code=422)


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
