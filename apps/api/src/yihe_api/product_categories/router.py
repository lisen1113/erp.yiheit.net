"""Category tree actions and effective schema projections."""

import hashlib
import json
from typing import Annotated, Any, Literal
from unicodedata import normalize
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yihe_api.audit.service import Actor, Category, audit_change
from yihe_api.auth.router import DB
from yihe_api.db.models import Company
from yihe_api.errors import ApiError, ErrorCode
from yihe_api.idempotency.service import FinalFailure, Operation, Success
from yihe_api.master_data.router import Idempotency, dispatch
from yihe_api.product_categories.models import ProductCategory as Row
from yihe_api.product_categories.schema import (
    Schema,
    compatibility,
    effective_schema,
    validate_attributes,
    validate_schema,
)
from yihe_api.rbac.policy import Principal
from yihe_api.rbac.router import Current

PATH = "/api/v1/product-categories"
MODULE = "product_category"
router = APIRouter(prefix="/product-categories", tags=["product-categories"])


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=2147483647)
    description: str | None = Field(default=None, max_length=500)
    attribute_schema: Schema = Field(default_factory=dict)


class Version(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version_no: int = Field(ge=1)


class Update(Version):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = Field(default=None, ge=0, le=2147483647)
    description: str | None = Field(default=None, max_length=500)
    attribute_schema: Schema | None = None


class Move(Version):
    parent_id: UUID | None


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str
    parent_id: UUID | None
    level: int
    path: str
    display_path: str
    sort_order: int
    description: str | None
    attribute_schema: Schema
    schema_version: int
    is_active: bool
    version_no: int


class TreeNode(View):
    children: list["TreeNode"] = Field(default_factory=list)


class Page(BaseModel):
    items: list[View]
    total: int
    tree: list[TreeNode] | None = None


def require(principal: Principal, action: str) -> None:
    principal.require(f"{MODULE}.{action}", company=action != "view")


def load(db: Session, company: UUID, *, lock: bool = False) -> dict[UUID, Row]:
    if lock:
        # NO KEY UPDATE serializes tree changes without conflicting with FK key-share locks.
        db.scalar(select(Company.id).where(Company.id == company).with_for_update(key_share=True))
    return {
        row.id: row
        for row in db.scalars(
            select(Row)
            .where(Row.company_id == company, Row.is_deleted.is_(False))
            .execution_options(populate_existing=True)
        )
    }


def one(rows: dict[UUID, Row], id: UUID) -> Row:
    if id not in rows:
        raise ApiError(ErrorCode.NOT_FOUND)
    return rows[id]


def lineage(rows: dict[UUID, Row], row: Row) -> list[Row]:
    path: list[Row] = []
    seen: set[UUID] = set()
    while True:
        if row.id in seen:
            raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_CYCLE)
        seen.add(row.id)
        path.append(row)
        if row.parent_id is None:
            return path[::-1]
        row = one(rows, row.parent_id)


def rules(path: list[Row]) -> Schema:
    result = validate_schema(path[0].attribute_schema)
    for row in path[1:]:
        result = effective_schema(result, row.attribute_schema)
    return result


def assert_category_available_for_new_product(db: Session, company_id: UUID, id: UUID) -> Row:
    rows = load(db, company_id, lock=True)
    row = one(rows, id)
    if not all(ancestor.is_active for ancestor in lineage(rows, row)):
        raise ApiError(ErrorCode.PRODUCT_CATEGORY_DISABLED)
    return row


def view(rows: dict[UUID, Row], row: Row) -> View:
    fields = {key: getattr(row, key) for key in View.model_fields if key != "display_path"}
    return View(**fields, display_path=" / ".join(item.name for item in lineage(rows, row)))


@router.get("", operation_id="listProductCategories")
def list_categories(
    db: DB,
    principal: Current,
    mode: Literal["tree", "flat"] = "flat",
    code: str | None = None,
    name: str | None = None,
    search: str | None = None,
    parent: UUID | None = None,
    active: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page:
    require(principal, "view")
    rows = load(db, principal.user.company_id)
    matched = [
        row
        for row in rows.values()
        if (code is None or code.casefold() in row.code.casefold())
        and (name is None or name.casefold() in row.name.casefold())
        and (search is None or search.casefold() in f"{row.code} {row.name}".casefold())
        and (parent is None or row.parent_id == parent)
        and (active is None or row.is_active == active)
    ]

    def ordering(row: Row) -> tuple[tuple[int, str], ...]:
        return tuple((item.sort_order, item.code) for item in lineage(rows, row))

    matched.sort(key=ordering)
    results = matched if mode == "tree" else matched[(page - 1) * page_size : page * page_size]
    items = [view(rows, row) for row in results]
    roots = None
    if mode == "tree":
        nodes = {item.id: TreeNode(**item.model_dump()) for item in items}
        roots = []
        for node in nodes.values():
            parent_node = nodes.get(node.parent_id) if node.parent_id is not None else None
            if parent_node is None:
                roots.append(node)
            else:
                parent_node.children.append(node)
    return Page(items=items, total=len(matched), tree=roots)


class SchemaPreview(BaseModel):
    parent_id: UUID | None = None
    attribute_schema: Schema
    sample: dict[str, Any] | None = None


class SchemaPreviewResult(BaseModel):
    effective_schema: Schema
    errors: dict[str, str]


@router.post("/schema-preview", operation_id="previewCategorySchema")
def preview_schema(payload: SchemaPreview, db: DB, principal: Current) -> SchemaPreviewResult:
    require(principal, "schema.manage")
    rows = load(db, principal.user.company_id)
    try:
        result = validate_schema(payload.attribute_schema)
        if payload.parent_id:
            parent = rules(lineage(rows, one(rows, payload.parent_id)))
            result = effective_schema(parent, result)
        errors = validate_attributes(result, payload.sample) if payload.sample is not None else {}
    except ValueError:
        raise ApiError(ErrorCode.PRODUCT_CATEGORY_SCHEMA_INVALID) from None
    return SchemaPreviewResult(effective_schema=result, errors=errors)


@router.get("/{id}", operation_id="getProductCategory")
def get_category(id: UUID, db: DB, principal: Current) -> View:
    require(principal, "view")
    rows = load(db, principal.user.company_id)
    return view(rows, one(rows, id))


@router.get("/{id}/effective-schema", operation_id="getEffectiveCategorySchema")
def get_effective_schema(id: UUID, db: DB, principal: Current) -> Schema:
    require(principal, "view")
    rows = load(db, principal.user.company_id)
    return rules(lineage(rows, one(rows, id)))


@router.post("/{id}/validate-attributes", operation_id="validateCategoryAttributes")
def validate(id: UUID, values: dict[str, Any], db: DB, principal: Current) -> dict[str, str]:
    return validate_attributes(get_effective_schema(id, db, principal), values)


def audit(
    db: Session,
    request: Request,
    actor: Actor,
    id: UUID,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    audit_change(
        db,
        request=request,
        actor=actor,
        company_id=actor.company_id,
        entity_id=id,
        entity_type=MODULE,
        category=Category.MASTER_DATA,
        action=f"{MODULE}.{action}",
        metadata=metadata,
    )


def flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        name = getattr(diag, "constraint_name", "")
        if name == "uq_product_category_company_id_code":
            raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_CODE_CONFLICT) from None
        if name == "uq_product_category_company_id_parent_id_normalized_name":
            raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_NAME_CONFLICT) from None
        raise


def normalize_identity(value: str, limit: int, *, fold: bool = False) -> str:
    value = normalize("NFKC", value).strip()
    value = value.casefold() if fold else value.upper()
    if not value or len(value) > limit:
        raise FinalFailure(ErrorCode.VALIDATION_ERROR)
    return value


def rebuild(rows: dict[UUID, Row], actor: Actor, changed_id: UUID) -> None:
    try:
        for row in rows.values():
            path = lineage(rows, row)
            rules(path)
            materialized = "/" + "/".join(str(item.id) for item in path)
            if row.path != materialized:
                row.path, row.level = materialized, len(path) - 1
                if row.id != changed_id:
                    row.version_no += 1
                    row.updated_by = actor.user_id
    except ValueError:
        raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_SCHEMA_INVALID) from None


def create_row(db: Session, request: Request, actor: Actor, payload: Create) -> Row:
    rows = load(db, actor.company_id, lock=True)
    if payload.parent_id and not all(
        item.is_active for item in lineage(rows, one(rows, payload.parent_id))
    ):
        raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_DISABLED)
    row = Row(
        id=uuid4(),
        company_id=actor.company_id,
        code=normalize_identity(payload.code, 32),
        name=payload.name,
        normalized_name=normalize_identity(payload.name, 300, fold=True),
        parent_id=payload.parent_id,
        sort_order=payload.sort_order,
        description=payload.description,
        path="",
        level=0,
        attribute_schema=payload.attribute_schema,
        schema_version=1,
        created_by=actor.user_id,
        updated_by=actor.user_id,
    )
    rows[row.id] = row
    rebuild(rows, actor, row.id)
    db.add(row)
    flush(db)
    audit(db, request, actor, row.id, "created")
    return row


def projection(payload: Create | Version) -> dict[str, Any]:
    result = payload.model_dump(mode="json", exclude_unset=True, exclude={"attribute_schema"})
    if isinstance(payload, (Create, Update)) and payload.attribute_schema is not None:
        try:
            validate_schema(payload.attribute_schema)
            encoded = json.dumps(payload.attribute_schema, sort_keys=True, allow_nan=False).encode()
        except ValueError:
            raise ApiError(ErrorCode.PRODUCT_CATEGORY_SCHEMA_INVALID) from None
        result["schema_sha256"] = hashlib.sha256(encoded).hexdigest()
    return result


@router.post("", status_code=201, operation_id="createProductCategory")
def create(
    payload: Create, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    require(principal, "create")
    if payload.attribute_schema:
        require(principal, "schema.manage")

    def work(tx: Session, actor: Actor) -> Success:
        row = create_row(tx, request, actor, payload)
        return Success(MODULE, row.id, status_code=201, location=f"{PATH}/{row.id}")

    return dispatch(
        db,
        request,
        Operation(f"{MODULE}.create", MODULE, None, PATH),
        projection(payload),
        work,
    )


def update_schema(db: Session, request: Request, actor: Actor, row: Row, schema: Schema) -> None:
    if schema == row.attribute_schema:
        return
    try:
        before, after = (
            validate_schema(row.attribute_schema),
            validate_schema(schema),
        )
    except ValueError:
        raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_SCHEMA_INVALID) from None
    old, new = before["properties"], after["properties"]
    changed = {name for name in old.keys() & new.keys() if old[name] != new[name]}
    audit(
        db,
        request,
        actor,
        row.id,
        "schema_updated",
        {
            "schema_added": sorted(new.keys() - old.keys()),
            "schema_removed": sorted(old.keys() - new.keys()),
            "schema_type_changed": sorted(k for k in changed if old[k]["type"] != new[k]["type"]),
            "schema_enum_changed": sorted(
                k for k in changed if old[k].get("enum") != new[k].get("enum")
            ),
            "schema_required_before": before["required"],
            "schema_required_after": after["required"],
            "schema_version_before": row.schema_version,
            "schema_version_after": row.schema_version + 1,
            "schema_compatibility": compatibility(before, after),
        },
    )
    row.attribute_schema = schema
    row.schema_version += 1


def change(
    id: UUID,
    payload: Version,
    action: str,
    request: Request,
    db: DB,
    principal: Principal,
) -> Response:
    require(principal, "disable" if action == "enable" else action)
    if isinstance(payload, Update) and "attribute_schema" in payload.model_fields_set:
        require(principal, "schema.manage")

    def work(tx: Session, actor: Actor) -> Success:
        rows = load(tx, actor.company_id, lock=True)
        row = one(rows, id)
        if row.version_no != payload.version_no:
            raise FinalFailure(ErrorCode.VERSION_CONFLICT)
        if isinstance(payload, Move):
            if payload.parent_id and not all(
                r.is_active for r in lineage(rows, one(rows, payload.parent_id))
            ):
                raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_DISABLED)
            row.parent_id = payload.parent_id
        elif isinstance(payload, Update):
            values = payload.model_dump(
                exclude_unset=True, exclude={"version_no", "attribute_schema"}
            )
            if any(values.get(field, True) is None for field in ("name", "sort_order")):
                raise FinalFailure(ErrorCode.VALIDATION_ERROR)
            for field, value in values.items():
                setattr(row, field, value)
            if "name" in values:
                row.normalized_name = normalize_identity(row.name, 300, fold=True)
            if payload.attribute_schema is not None:
                update_schema(tx, request, actor, row, payload.attribute_schema)
        else:
            if action == "disable" and any(
                r.parent_id == id and r.is_active for r in rows.values()
            ):
                raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_HAS_ACTIVE_CHILDREN)
            if action == "enable" and any(not r.is_active for r in lineage(rows, row)[:-1]):
                raise FinalFailure(ErrorCode.PRODUCT_CATEGORY_DISABLED)
            row.is_active = action == "enable"
        rebuild(rows, actor, id)
        row.version_no += 1
        row.updated_by = actor.user_id
        flush(tx)
        audit(
            tx,
            request,
            actor,
            id,
            {"edit": "updated", "move": "moved", "enable": "enabled", "disable": "disabled"}[
                action
            ],
        )
        return Success(MODULE, id)

    path = f"{PATH}/{{id}}" if action == "edit" else f"{PATH}/{{id}}/{action}"
    return dispatch(
        db,
        request,
        Operation(f"{MODULE}.{action}", MODULE, id, path),
        projection(payload),
        work,
    )


@router.patch("/{id}", operation_id="updateProductCategory")
def update(
    id: UUID, payload: Update, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return change(id, payload, "edit", request, db, principal)


@router.post("/{id}/move", operation_id="moveProductCategory")
def move(
    id: UUID, payload: Move, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return change(id, payload, "move", request, db, principal)


@router.post("/{id}/enable", operation_id="enableProductCategory")
def enable(
    id: UUID, payload: Version, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return change(id, payload, "enable", request, db, principal)


@router.post("/{id}/disable", operation_id="disableProductCategory")
def disable(
    id: UUID, payload: Version, request: Request, db: DB, principal: Current, key: Idempotency
) -> Response:
    return change(id, payload, "disable", request, db, principal)
