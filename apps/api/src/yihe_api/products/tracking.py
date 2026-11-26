"""Stable transaction-provider contract for SKU tracking-mode changes."""

from collections.abc import Iterable
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from yihe_api.errors import ApiError, ErrorCode
from yihe_api.products.models import ProductSKU


class TransactionFact(StrEnum):
    PURCHASE_RECEIPT_POSTED = "purchase_receipt_posted"
    SALES_SHIPMENT_POSTED = "sales_shipment_posted"
    SALES_RETURN_POSTED = "sales_return_posted"
    PURCHASE_RETURN_POSTED = "purchase_return_posted"
    INVENTORY_ADJUSTMENT = "inventory_adjustment"
    INVENTORY_TRANSFER = "inventory_transfer"
    SERIAL_MOVEMENT = "serial_movement"
    BATCH_MOVEMENT = "batch_movement"
    OPENING_INVENTORY_CONFIRMED = "opening_inventory_confirmed"


class TransactionFactProvider(Protocol):
    def __call__(
        self, db: Session, company_id: UUID, sku_id: UUID
    ) -> Iterable[TransactionFact]: ...


# Each future posting subsystem registers its own provider at application assembly.
TRANSACTION_FACT_PROVIDERS: tuple[TransactionFactProvider, ...] = ()


def lock_product_sku(db: Session, company_id: UUID, sku_id: UUID) -> ProductSKU:
    """Use the same lock before either inspecting facts or creating the first fact."""
    sku = db.scalar(
        select(ProductSKU)
        .where(
            ProductSKU.company_id == company_id,
            ProductSKU.id == sku_id,
            ProductSKU.is_deleted.is_(False),
        )
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if sku is None:
        raise ApiError(ErrorCode.NOT_FOUND)
    return sku


def has_persisted_product_transactions(
    db: Session,
    sku: ProductSKU,
    providers: Iterable[TransactionFactProvider] | None = None,
) -> bool:
    """Call within the transaction holding the SKU lock. Balances are irrelevant."""
    registered = TRANSACTION_FACT_PROVIDERS if providers is None else providers
    return any(
        fact in TransactionFact
        for provider in registered
        for fact in provider(db, sku.company_id, sku.id)
    )
