"""Single versioned storage port, backed by S3-compatible MinIO or Tencent COS."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]


class StorageError(RuntimeError):
    """Provider failure with a deliberately generic public representation."""


class BadObject(StorageError):
    """Object violates validation constraints."""


@dataclass(frozen=True)
class Version:
    id: str
    size: int
    mime: str


class Storage(Protocol):
    bucket: str
    provider: str

    def put_url(self, key: str, mime: str, length: int, seconds: int) -> str: ...
    def get_url(self, key: str, version: str, filename: str, seconds: int) -> str: ...
    def stat(self, key: str) -> Version: ...
    def read(self, key: str, version: str, maximum: int) -> bytes: ...
    def delete_version(self, key: str, version: str) -> None: ...


@dataclass(repr=False)
class S3Storage:
    bucket: str
    provider: str
    internal: Any
    public: Any

    def put_url(self, key: str, mime: str, length: int, seconds: int) -> str:
        return self.sign("put_object", seconds, Key=key, ContentType=mime, ContentLength=length)

    def get_url(self, key: str, version: str, filename: str, seconds: int) -> str:
        return self.sign(
            "get_object",
            seconds,
            Key=key,
            VersionId=version,
            ResponseContentDisposition=filename,
            ResponseContentType="application/octet-stream",
            ResponseCacheControl="private, no-store",
        )

    def sign(self, operation: str, seconds: int, **params: object) -> str:
        if not 1 <= seconds <= 300:
            raise ValueError("Invalid storage signature duration")
        try:
            return cast(
                str,
                self.public.generate_presigned_url(
                    operation,
                    Params={"Bucket": self.bucket, **params},
                    ExpiresIn=seconds,
                ),
            )
        except (BotoCoreError, ClientError):
            raise StorageError("Cannot sign object request") from None

    def stat(self, key: str) -> Version:
        try:
            value = self.internal.head_object(Bucket=self.bucket, Key=key)
            headers = value["ResponseMetadata"]["HTTPHeaders"]
            version = value.get("VersionId") or headers.get("x-cos-version-id")
            if not isinstance(version, str) or version in {"", "null"} or len(version) > 1024:
                raise StorageError("Object bucket versioning is required")
            return Version(version, int(value["ContentLength"]), str(value["ContentType"]))
        except (BotoCoreError, ClientError):
            raise StorageError("Versioned object is unavailable") from None

    def read(self, key: str, version: str, maximum: int) -> bytes:
        try:
            obj = self.internal.get_object(Bucket=self.bucket, Key=key, VersionId=version)
            with obj["Body"] as stream:
                if int(obj["ContentLength"]) > maximum:
                    raise BadObject("Object exceeds limit")
                content = cast(bytes, stream.read(maximum + 1))
            if len(content) > maximum:
                raise BadObject("Object exceeds limit")
            return content
        except (BotoCoreError, ClientError):
            raise StorageError("Object version is unavailable") from None

    def delete_version(self, key: str, version: str) -> None:
        try:
            self.internal.delete_object(Bucket=self.bucket, Key=key, VersionId=version)
        except (BotoCoreError, ClientError):
            raise StorageError("Object version removal failed") from None


@lru_cache
def storage() -> Storage:
    try:
        provider = os.environ["ERP_STORAGE_PROVIDER"]
        region = os.environ["ERP_STORAGE_REGION"]
        bucket = os.environ["ERP_STORAGE_BUCKET"]
        key = os.environ["ERP_STORAGE_ACCESS_KEY"]
        secret = os.environ["ERP_STORAGE_SECRET_KEY"]
        token = os.environ.get("ERP_STORAGE_SESSION_TOKEN") or None
        if not bucket or not key or not secret:
            raise ValueError
        if provider == "cos":
            if not region.startswith("ap-") or not region.replace("-", "").isalnum():
                raise ValueError
            endpoint = public = f"https://cos.{region}.myqcloud.com"
            style = "virtual"
        elif provider == "minio":
            endpoint = os.environ["ERP_STORAGE_ENDPOINT"]
            public = os.environ.get("ERP_STORAGE_PUBLIC_ENDPOINT") or endpoint
            for url in (endpoint, public):
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.hostname
                    or parsed.path
                    or parsed.username
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError
            style = "path"
        else:
            raise ValueError

        def client(url: str) -> Any:
            return boto3.client(
                "s3",
                endpoint_url=url,
                region_name=region,
                aws_access_key_id=key,
                aws_secret_access_key=secret,
                aws_session_token=token,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": style},
                    retries={"total_max_attempts": 1},
                    connect_timeout=3,
                    read_timeout=6,
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )

        return S3Storage(bucket, provider, client(endpoint), client(public))
    except (KeyError, ValueError, BotoCoreError, ClientError):
        raise StorageError("Storage runtime configuration is unavailable") from None
