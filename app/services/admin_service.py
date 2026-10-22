"""Admin operations. Never imports FastAPI."""

import logging
import uuid
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.exceptions import WalletNotFoundError
from app.models.enums import AuditAction, WalletStatus
from app.models.wallet import Wallet
from app.repositories.wallet_repo import WalletRepository
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class FreezeResult(NamedTuple):
    wallet: Wallet
    changed: bool  # False = the wallet already had the requested status


class AdminService:
    def __init__(
        self, db: Session, wallet_repo: WalletRepository, audit_service: AuditService
    ) -> None:
        self.db = db
        self.wallet_repo = wallet_repo
        self.audit_service = audit_service

    def set_wallet_frozen(
        self,
        admin_id: uuid.UUID,
        wallet_id: uuid.UUID,
        frozen: bool,
        reason: str | None = None,
    ) -> FreezeResult:
        """Freeze or unfreeze a wallet. One DB transaction, safe to repeat."""
        reason = (reason or "").strip() or None

        # 1. Lock the wallet row so two admins can't change it at the same time.
        wallet = self.wallet_repo.get_by_id_for_update(wallet_id)
        if wallet is None:
            logger.warning("admin_wallet_not_found", extra={"admin_id": str(admin_id)})
            raise WalletNotFoundError()

        # 2. Already in the requested state: nothing to change, nothing to audit.
        target = WalletStatus.FROZEN if frozen else WalletStatus.ACTIVE
        if wallet.status == target:
            return FreezeResult(wallet, changed=False)

        # 3. Change the wallet and stage the audit row in the same DB transaction.
        previous = wallet.status
        wallet.status = target.value
        wallet.version += 1
        self.audit_service.record(
            AuditAction.WALLET_FROZEN if frozen else AuditAction.WALLET_UNFROZEN,
            entity_type="wallet",
            entity_id=wallet.id,
            actor_id=admin_id,
            metadata={
                "previous_status": previous,
                "new_status": target.value,
                "reason": reason,
                "owner_user_id": wallet.user_id,
            },
        )
        self.db.commit()
        self.db.refresh(wallet)  # reload DB-generated values such as updated_at

        logger.info(
            "wallet_frozen" if frozen else "wallet_unfrozen",
            extra={"wallet_id": str(wallet.id), "admin_id": str(admin_id)},
        )
        return FreezeResult(wallet, changed=True)
