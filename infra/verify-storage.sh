#!/usr/bin/env bash
set -euo pipefail
cmd=(docker compose -p t009-storage -f compose.yaml -f infra/compose.storage.yaml)
"${cmd[@]}" up -d --build --wait minio
uv run --frozen python - <<'PY'
import boto3
import os

s3 = boto3.client("s3", endpoint_url=os.environ["ERP_STORAGE_ENDPOINT"],
                  aws_access_key_id=os.environ["ERP_STORAGE_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["ERP_STORAGE_SECRET_KEY"],
                  region_name="us-east-1")
bucket = os.environ["ERP_STORAGE_BUCKET"]
s3.create_bucket(Bucket=bucket)
s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status":"Enabled"})
s3.put_object(Bucket=bucket, Key="ci/probe", Body=b"minio-persistence",
              ContentType="application/octet-stream")
PY
"${cmd[@]}" stop minio
"${cmd[@]}" rm -f minio
"${cmd[@]}" up -d --wait minio
