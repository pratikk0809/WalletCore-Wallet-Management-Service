"""TEMPORARY routes to test error handling. Delete once real endpoints exist."""
from fastapi import APIRouter

from app.exceptions import InsufficientFundsError

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/domain-error")
def domain_error() -> None:
    raise InsufficientFundsError()


@router.get("/crash")
def crash() -> None:
    1 / 0  # deliberate bug -> should produce a clean 500


@router.get("/validate")
def validate(amount: int) -> dict[str, int]:
    return {"amount": amount}