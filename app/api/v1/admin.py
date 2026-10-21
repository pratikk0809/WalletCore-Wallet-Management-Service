"""Admin endpoints. HTTP only: parse, call the service, return."""
import uuid

from fastapi import APIRouter, Depends

from app.api.deps import get_admin_service, get_current_admin
from app.models.user import User
from app.schemas.admin import AdminWalletResponse, WalletFreezeRequest
from app.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/wallets/{wallet_id}/freeze", response_model=AdminWalletResponse)
def set_wallet_frozen(
    wallet_id: uuid.UUID,
    body: WalletFreezeRequest,
    admin: User = Depends(get_current_admin),
    service: AdminService = Depends(get_admin_service),
) -> AdminWalletResponse:
    """Freeze (`frozen: true`) or unfreeze (`frozen: false`) a wallet. Admin only.

    A frozen wallet can't send or receive money. 200 even if nothing changed
    (see `changed`), 403 = not an admin, 404 = unknown wallet.
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