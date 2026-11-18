"""Public authentication contract. Sensitive inputs have redacted representations."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, StringConstraints

Password = Annotated[SecretStr, Field(min_length=1, max_length=128)]
Code = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Login(BaseModel):
    username: Username
    password: Password


class ConfirmPassword(BaseModel):
    challenge: Annotated[SecretStr, Field(min_length=32, max_length=128)] | None = None
    password: Password


class PasswordChange(BaseModel):
    password: Password
    new_password: Annotated[SecretStr, Field(min_length=12, max_length=128)]


class TotpCode(BaseModel):
    challenge: Annotated[SecretStr, Field(min_length=32, max_length=128)] | None = None
    code: Code


class ChallengeVerify(TotpCode):
    challenge: Annotated[SecretStr, Field(min_length=32, max_length=128)]


class Access(BaseModel):
    status: Literal["authenticated"] = "authenticated"
    access_token: str
    expires_in: int


class Challenge(BaseModel):
    status: Literal["mfa_required", "mfa_setup_required"]
    challenge: str
    expires_in: int


class Setup(BaseModel):
    otpauth_uri: str


class CurrentUser(BaseModel):
    user_id: UUID
    company_id: UUID
    employee_id: UUID
    username: str
    mfa_required: bool
    mfa_enabled: bool
