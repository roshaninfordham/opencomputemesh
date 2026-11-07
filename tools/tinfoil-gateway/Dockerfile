# syntax=docker/dockerfile:1
#
# Stateless by design: no volume, no database, no writes. The only state is the
# verified enclave connection held in memory, which is why this image can run in
# an enclave whose root filesystem is read-only.

FROM rust:1.99-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src ./src
# The SDK is a git dependency, so this stage needs network access. The resulting
# binary does not: it pins TLS to the verified enclave at runtime.
RUN cargo build --release --locked

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /app/target/release/ocm-tinfoil-gateway /usr/local/bin/gateway
# A container must listen on all interfaces to be reachable by the shim.
ENV GATEWAY_BIND=0.0.0.0:8787
EXPOSE 8787
USER nonroot:nonroot
ENTRYPOINT ["/usr/local/bin/gateway"]
