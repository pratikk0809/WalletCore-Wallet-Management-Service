"""Admin endpoints. HTTP only: parse, call the service, return."""
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import get_admin_service, get_current_admin
from app.api.responses import errors
from app.models.user import User
from app.schemas.admin import AdminWalletResponse, WalletFreezeRequest
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/wallets/{wallet_id}/freeze",
    response_model=AdminWalletResponse,
    summary="Freeze or unfreeze a wallet",
    responses=errors(
        401, 403, 404, 422,
        custom={
            403: ("The caller is not an admin.", "FORBIDDEN", "Admin access required."),
            404: ("No wallet with this ID.", "WALLET_NOT_FOUND", "Wallet not found."),
        },
    ),
)
def set_wallet_frozen(
    wallet_id: uuid.UUID,
    body: WalletFreezeRequest,
    admin: User = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminWalletResponse:
    """Freeze (`frozen: true`) or unfreeze (`frozen: false`) a wallet. Admin only.

    A frozen wallet can't send or receive money. Returns 200 even if nothing changed
    (see `changed`).
    """
    result = service.set_wallet_frozen(admin.id, wallet_id, body.frozen, body.reason)
    wallet = result.wallet
    return AdminWalletResponse(
        id=wallet.id,
        user_id=wallet.user_id,
        currency=wallet.currency,
        balance=wallet.balance,
        status=wallet.status,
        updated_at=wallet.updated_at,
        changed=result.changed,
    )