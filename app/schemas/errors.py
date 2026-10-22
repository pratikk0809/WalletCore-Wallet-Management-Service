"""The standard error body, as a schema (used for documentation only)."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str  # stable, machine-readable, e.g. INSUFFICIENT_FUNDS
    message: str  # human-readable
    request_id: str | None  # quote this when reporting a problem
    details: list[ErrorDetail] | None = None  # only on 422 validation errors


class ErrorResponse(BaseModel):
    error: ErrorBody
