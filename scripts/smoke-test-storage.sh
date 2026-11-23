#!/usr/bin/env bash
set -euo pipefail
umask 077

project=yihe-erp-test
sha=${1:?}
tmp=$(mktemp -d)
minio=t011-minio-$sha
clam=t011-clamav-$sha
volume=t011-minio-$sha
cleanup() {
  docker rm -f "$minio" "$clam" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  rm -rf -- "$tmp"
}
for name in "$minio" "$clam"; do
  if docker container inspect "$name" >/dev/null 2>&1; then exit 1; fi
done
if docker volume inspect "$volume" >/dev/null 2>&1; then exit 1; fi
trap cleanup EXIT
network=$project-data
docker network inspect "$network" >/dev/null
printf 'MINIO_ROOT_USER=t011synthetic\nMINIO_ROOT_PASSWORD=%s\n' "$(openssl rand -hex 32)" > "$tmp/minio.env"
docker volume create "$volume" >/dev/null
start_minio() {
  docker run -d --name "$minio" --network "$network" --memory 512m \
    --env-file "$tmp/minio.env" -v "$volume:/data" "yihe-erp-minio:$sha" server /data >/dev/null
  for _ in {1..30}; do
    if docker exec "$minio" wget -q -O /dev/null http://127.0.0.1:9000/minio/health/ready; then return; fi
    sleep 2
  done
  return 1
}
minio_probe() {
  docker run --rm -i --network "$network" --env-file "$tmp/minio.env" \
    -e "T011_MINIO_HOST=$minio" -e "T011_MINIO_STAGE=$1" \
    -v "$tmp:/probe" --user 0 "yihe-erp-api:$sha" python - <<'PY'
import hashlib
import io
import os
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image

bucket = "t011-acceptance"
key = "synthetic.png"
record = Path("/probe/version")
client = boto3.client(
    "s3",
    endpoint_url=f"http://{os.environ['T011_MINIO_HOST']}:9000",
    aws_access_key_id=os.environ["MINIO_ROOT_USER"],
    aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
    region_name="us-east-1",
    config=Config(s3={"addressing_style": "path"}, retries={"total_max_attempts": 1}),
)
image = io.BytesIO()
Image.new("RGB", (12, 12), "white").save(image, "PNG")
png = image.getvalue()
if os.environ["T011_MINIO_STAGE"] == "upload":
    client.create_bucket(Bucket=bucket)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    result = client.put_object(Bucket=bucket, Key=key, Body=png, ContentType="image/png")
    record.write_text(result["VersionId"])
version = record.read_text()
assert version and version != "null"
head = client.head_object(Bucket=bucket, Key=key, VersionId=version)
assert head["VersionId"] == version and head["ContentLength"] == len(png)
with client.get_object(Bucket=bucket, Key=key, VersionId=version)["Body"] as body:
    assert hashlib.sha256(body.read()).digest() == hashlib.sha256(png).digest()
print("MinIO versioned HEAD, download and SHA-256 passed:", os.environ["T011_MINIO_STAGE"])
PY
}
start_minio
minio_probe upload
docker rm -f "$minio" >/dev/null
start_minio
minio_probe recreated
docker rm -f "$minio" >/dev/null

docker pull docker.io/clamav/clamav:stable
image=$(docker image inspect -f '{{ index .RepoDigests 0 }}' docker.io/clamav/clamav:stable)
[[ "$image" =~ @sha256:[a-f0-9]{64}$ ]]
# The edge network gives freshclam outbound access. No scanner port is published.
docker run -d --name "$clam" --network "$project-edge" --memory 1536m "$image" >/dev/null
for _ in {1..60}; do
  if docker exec "$clam" clamdscan --ping=1 >/dev/null 2>&1; then break; fi
  sleep 5
done
docker exec "$clam" clamdscan --ping=1 >/dev/null
printf '%s\n' 'synthetic clean T011 file' | docker exec -i "$clam" clamdscan - >/dev/null
# Use the standard harmless EICAR test string to verify the live scanner.
set +e
printf 'X5O!P%%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' |
  docker exec -i "$clam" clamdscan - >/dev/null
result=$?
set -e
[[ "$result" == 1 ]]
docker rm -f "$clam" >/dev/null
docker run --rm --network "$project-edge" -e "ERP_CLAMD_HOST=$clam" "yihe-erp-api:$sha" \
  python -c 'from yihe_api.files.validation import Verdict, scan; assert scan(b"synthetic offline file") == Verdict.UNAVAILABLE'
printf '%s\n' 'ClamAV clean file, live EICAR detection and application offline verdict passed.'
