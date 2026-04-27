#!/usr/bin/env bash
set -euo pipefail

# AIOpsOS Docker Image Build Script
# Builds server and web images with version tagging.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

VERSION="${VERSION:-$(date +%Y%m%d-%H%M%S)}"
DOCKER_REGISTRY="${DOCKER_REGISTRY:-}"
NO_CACHE="${NO_CACHE:-}"
PUSH="${PUSH:-0}"
SERVICES="${SERVICES:-server web}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*" >&2; }

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --version VERSION    Image version tag (default: YYYYMMDD-HHMMSS)
  --registry REGISTRY  Docker registry prefix (e.g., docker.1ms.run)
  --push              Push images after building
  --no-cache          Build without Docker layer cache
  --services LIST     Comma-separated services (default: server,web)
  -h, --help          Show this help

Examples:
  $0                                    # Build all with timestamp tag
  $0 --version v1.0.0 --push           # Build, tag v1.0.0, push
  $0 --registry docker.1ms.run --push  # Push to mirror registry
  $0 --services server --no-cache       # Rebuild only server, no cache
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)    VERSION="$2"; shift 2 ;;
        --registry)   DOCKER_REGISTRY="$2"; shift 2 ;;
        --push)       PUSH=1; shift ;;
        --no-cache)   NO_CACHE="--no-cache"; shift ;;
        --services)   SERVICES="${2//,/ }"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *)            err "Unknown option: $1"; usage; exit 1 ;;
    esac
done

log "Building AIOpsOS images (version: ${VERSION})"
log "Services: ${SERVICES}"

cd "$ROOT_DIR"

for service in $SERVICES; do
    case "$service" in
        server) DOCKERFILE="deploy/Dockerfile.server" ;;
        web)    DOCKERFILE="deploy/Dockerfile.web" ;;
        *)      err "Unknown service: $service"; exit 1 ;;
    esac

    IMAGE_NAME="aiopsos-${service}"
    IMAGE_TAG="${IMAGE_NAME}:${VERSION}"
    LATEST_TAG="${IMAGE_NAME}:latest"

    log "Building ${IMAGE_TAG} ..."

    docker build \
        ${NO_CACHE} \
        -t "${IMAGE_TAG}" \
        -t "${LATEST_TAG}" \
        -f "${DOCKERFILE}" \
        .

    log "Built ${IMAGE_TAG}"

    if [[ -n "$DOCKER_REGISTRY" ]]; then
        REGISTRY_TAG="${DOCKER_REGISTRY}/${IMAGE_TAG}"
        REGISTRY_LATEST="${DOCKER_REGISTRY}/${LATEST_TAG}"
        log "Tagging ${REGISTRY_TAG}"
        docker tag "${IMAGE_TAG}" "${REGISTRY_TAG}"
        docker tag "${IMAGE_TAG}" "${REGISTRY_LATEST}"

        if [[ "$PUSH" == "1" ]]; then
            log "Pushing ${REGISTRY_TAG}"
            docker push "${REGISTRY_TAG}"
            docker push "${REGISTRY_LATEST}"
        fi
    elif [[ "$PUSH" == "1" ]]; then
        warn "No registry specified, pushing local tags (set --registry)"
        docker push "${IMAGE_TAG}"
        docker push "${LATEST_TAG}"
    fi
done

log "Build complete."

docker images --filter=reference='aiopsos-*' \
    --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}'
