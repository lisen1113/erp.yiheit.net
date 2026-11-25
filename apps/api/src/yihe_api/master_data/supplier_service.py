"""Shared eligibility validation for future purchase transaction services."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from yihe_api.errors import ApiError, ErrorCode
from yihe_api.master_data.models import BusinessPartner
from yihe_api.master_data.supplier_models import SupplierProfile


def assert_supplier_available_for_new_transaction(
    db: Session, company_id: UUID, supplier_id: UUID
) -> SupplierProfile:
    """Check eligibility in the same transaction that creates the downstream document."""
    row = db.execute(
        select(SupplierProfile, BusinessPartner)
        .join(BusinessPartner, BusinessPartner.id == SupplierProfile.business_partner_id)
        .where(
            SupplierProfile.id == supplier_id,
            SupplierProfile.company_id == company_id,
            SupplierProfile.is_deleted.is_(False),
        )
        .with_for_update(of=(BusinessPartner, SupplierProfile), read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    profile, partner = row
    if (
        not profile.is_active
        or not partner.is_active
        or partner.is_deleted
        or partner.partner_type not in {"SUPPLIER", "BOTH"}
    ):
        raise ApiError(ErrorCode.SUPPLIER_UNAVAILABLE)
    return profile
