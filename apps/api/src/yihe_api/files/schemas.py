from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from yihe_api.files.validation import Kind


class State(StrEnum):
    UPLOADING = "UPLOADING"
    AVAILABLE = "AVAILABLE"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    DELETED_FROM_STORAGE = "DELETED_FROM_STORAGE"


class Purpose(StrEnum):
    LOGO_HORIZONTAL = "LOGO_HORIZONTAL"
    LOGO_COMPACT = "LOGO_COMPACT"
    LOGO_LIGHT_BACKGROUND = "LOGO_LIGHT_BACKGROUND"
    LOGO_DARK_BACKGROUND = "LOGO_DARK_BACKGROUND"
    DOCUMENT_BACKGROUND = "DOCUMENT_BACKGROUND"
    DOCUMENT_FOOTER = "DOCUMENT_FOOTER"
    EMAIL_HEADER = "EMAIL_HEADER"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Upload(Input):
    asset_type: Kind
    filename: Annotated[str, Field(min_length=1, max_length=180)]
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    size_bytes: Annotated[int, Field(gt=0, le=50 * 1024**2, strict=True)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    sensitivity: Literal["STANDARD", "SENSITIVE"] = "STANDARD"


class VersionInput(Input):
    version_no: Annotated[int, Field(ge=1)]


class BrandCreate(Input):
    file_asset_id: UUID
    asset_code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,49}$")]
    asset_type: Purpose
    language: Literal["zh-CN", "en"] = "zh-CN"


class FileView(BaseModel):
    id: UUID
    asset_type: Kind
    display_name: str
    content_type: str
    size_bytes: int
    sha256: str | None
    status: State
    version_no: int


class BrandView(BaseModel):
    id: UUID
    file_asset_id: UUID
    asset_code: str
    asset_type: Purpose
    language: str
    version: int
    version_no: int
    is_active: bool


class Ticket(BaseModel):
    url: str
    expires_in: int
    required_headers: dict[str, str] = {}


class Receipt(BaseModel):
    resource_id: UUID
    resource_type: str
    status: str
