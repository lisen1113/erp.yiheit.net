# The community runtime is built from the official MinIO security release.
FROM golang:1.26-bookworm AS build
RUN CGO_ENABLED=0 go install github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z

FROM alpine:3.22
RUN mkdir -p /data && chown 10001:10001 /data
ENV HOME=/data MINIO_BROWSER=off
COPY --from=build /go/bin/minio /usr/local/bin/minio
USER 10001:10001
ENTRYPOINT ["minio"]
