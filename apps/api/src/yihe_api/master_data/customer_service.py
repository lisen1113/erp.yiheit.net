"""Shared eligibility validation for future sales transaction services."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from yihe_api.errors import ApiError, ErrorCode
from yihe_api.master_data.customer_models import CustomerProfile
from yihe_api.master_data.models import BusinessPartner


def assert_customer_available_for_new_transaction(
    db: Session, company_id: UUID, customer_id: UUID
) -> CustomerProfile:
    """Check eligibility in the same transaction that creates the downstream document."""
    row = db.execute(
        select(CustomerProfile, BusinessPartner)
        .join(BusinessPartner, BusinessPartner.id == CustomerProfile.business_partner_id)
        .where(
            CustomerProfile.id == customer_id,
            CustomerProfile.company_id == company_id,
            CustomerProfile.is_deleted.is_(False),
        )
        .with_for_update(of=(BusinessPartner, CustomerProfile), read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    profile, partner = row
    if (
        not profile.is_active
        or not partner.is_active
        or partner.is_deleted
        or partner.partner_type not in {"CUSTOMER", "BOTH"}
    ):
        raise ApiError(ErrorCode.CUSTOMER_UNAVAILABLE)
    return profile
