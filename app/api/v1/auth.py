"""Auth endpoints. HTTP only: parse the request, call the service, return the response."""
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import get_auth_service
from app.api.rate_limits import limit_login, limit_register
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenResponse, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=201,
    dependencies=[Depends(limit_register)],
)
def register(
    body: RegisterRequest, service: AuthService = Depends(get_auth_service)
) -> User:
    """Create an account and its wallet. Rate limited per IP."""
    return service.register(body.email, body.password)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(limit_login)],
)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """OAuth2 password flow: form fields `username` (your email) and `password`. Rate limited per IP."""
    token = service.login(form.username, form.password)
    return TokenResponse(access_token=token)