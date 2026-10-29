#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-minikube}
IMPLEMENTATION=${STREAMING_IMPLEMENTATION:-${2:-python}}
IMAGE_TAG=${IMAGE_TAG:-local}
MINIKUBE_PROFILE=${MINIKUBE_PROFILE:-db-service-refarch}
Q_IMAGE_REPOSITORY=${Q_IMAGE_REPOSITORY:-sim-streaming-pnl-q}
KDBX_BASE_IMAGE=${KDBX_BASE_IMAGE:-portal.dl.kx.com/kxi-kdbx-base:1.19.3}
RT_QPK_VERSION=${RT_QPK_VERSION:-1.18.1}
RT_QPK_URL=${RT_QPK_URL:-https://portal.dl.kx.com/assets/raw/rt}

python_components=(marketdata oms pnl)
build_tmp_dir=""

cleanup() {
  if [ -n "$build_tmp_dir" ] && [ -d "$build_tmp_dir" ]; then
    rm -rf "$build_tmp_dir"
  fi
}

trap cleanup EXIT

image_name() {
  local component=$1
  local prefix=""

  if [ -n "${IMAGE_REGISTRY:-}" ]; then
    prefix="${IMAGE_REGISTRY%/}/"
  fi

  printf '%ssim-%s:%s' "$prefix" "$component" "$IMAGE_TAG"
}

q_image_name() {
  local prefix=""

  if [ -n "${IMAGE_REGISTRY:-}" ]; then
    prefix="${IMAGE_REGISTRY%/}/"
  fi

  printf '%s%s:%s' "$prefix" "$Q_IMAGE_REPOSITORY" "$IMAGE_TAG"
}

build_with_docker() {
  local component=$1
  docker build \
    -t "$(image_name "$component")" \
    -f "$ROOT_DIR/python/$component/Dockerfile" \
    "$ROOT_DIR/python"
}

prepare_q_build_context() {
  local rt_dir=""
  local qpk_file=""
  local qpk_root=""

  build_tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/streaming-pnl-q.XXXXXX")

  if [ -n "${RT_QPK_DIR:-}" ]; then
    if [ ! -f "$RT_QPK_DIR/startq.q" ]; then
      echo "RT_QPK_DIR must contain startq.q: $RT_QPK_DIR" >&2
      exit 1
    fi
    rt_dir=$(cd "$RT_QPK_DIR" && pwd)
    echo "Using unpacked rt.qpk from $rt_dir"
  else
    command -v curl >/dev/null 2>&1 || {
      echo "curl is required to download rt.qpk" >&2
      exit 1
    }
    command -v unzip >/dev/null 2>&1 || {
      echo "unzip is required to unpack rt.qpk" >&2
      exit 1
    }

    qpk_root="$build_tmp_dir/qpk"
    qpk_file="$build_tmp_dir/rt.$RT_QPK_VERSION.qpk"
    mkdir -p "$qpk_root"

    echo "Downloading rt.qpk v$RT_QPK_VERSION"
    curl -fsSL \
      "$RT_QPK_URL/$RT_QPK_VERSION/rt.$RT_QPK_VERSION.qpk" \
      -o "$qpk_file"
    unzip -q "$qpk_file" -d "$qpk_root"

    if [ -f "$qpk_root/rt/startq.q" ]; then
      rt_dir="$qpk_root/rt"
    elif [ -f "$qpk_root/startq.q" ]; then
      rt_dir="$qpk_root"
    else
      echo "The downloaded rt.qpk did not contain startq.q" >&2
      exit 1
    fi
  fi

  Q_BUILD_CONTEXT="$build_tmp_dir/context"
  mkdir -p "$Q_BUILD_CONTEXT/q" "$Q_BUILD_CONTEXT/rt"
  cp "$ROOT_DIR/q/"*.q "$Q_BUILD_CONTEXT/q/"
  cp -R "$rt_dir/." "$Q_BUILD_CONTEXT/rt/"
}

build_q_with_docker() {
  prepare_q_build_context
  docker build \
    --build-arg "KDBX_BASE_IMAGE=$KDBX_BASE_IMAGE" \
    -t "$(q_image_name)" \
    -f "$ROOT_DIR/q/Dockerfile" \
    "$Q_BUILD_CONTEXT"
}

load_minikube_image() {
  local image=$1
  echo "Loading $image into Minikube profile '$MINIKUBE_PROFILE' (this may take several minutes)"
  minikube -p "$MINIKUBE_PROFILE" image load --daemon "$image"
  echo "Loaded $image"
}

verify_minikube_image() {
  local image=$1
  local normalized_image=$image
  local minikube_images

  if [[ $image != */* ]]; then
    normalized_image="docker.io/library/$image"
  fi

  minikube_images=$(minikube -p "$MINIKUBE_PROFILE" image ls)
  if ! grep -Fqx "$image" <<<"$minikube_images" \
    && ! grep -Fqx "$normalized_image" <<<"$minikube_images"; then
    echo "ERROR: $image was not found in Minikube profile '$MINIKUBE_PROFILE'" >&2
    exit 1
  fi
}

case "$IMPLEMENTATION" in
  python|q|all) ;;
  *)
    echo "STREAMING_IMPLEMENTATION must be python, q, or all" >&2
    exit 2
    ;;
esac

case "$MODE" in
  minikube)
    if [ "$IMPLEMENTATION" = "python" ] || [ "$IMPLEMENTATION" = "all" ]; then
      for component in "${python_components[@]}"; do
        image=$(image_name "$component")
        echo "Building $image with Docker"
        build_with_docker "$component"
        load_minikube_image "$image"
        verify_minikube_image "$image"
      done
    fi

    if [ "$IMPLEMENTATION" = "q" ] || [ "$IMPLEMENTATION" = "all" ]; then
      image=$(q_image_name)
      echo "Building $image with KDB-X base image $KDBX_BASE_IMAGE"
      build_q_with_docker
      load_minikube_image "$image"
      verify_minikube_image "$image"
    fi
    ;;
  docker)
    if [ "$IMPLEMENTATION" = "python" ] || [ "$IMPLEMENTATION" = "all" ]; then
      for component in "${python_components[@]}"; do
        echo "Building $(image_name "$component")"
        build_with_docker "$component"
      done
    fi

    if [ "$IMPLEMENTATION" = "q" ] || [ "$IMPLEMENTATION" = "all" ]; then
      echo "Building $(q_image_name) with KDB-X base image $KDBX_BASE_IMAGE"
      build_q_with_docker
    fi
    ;;
  push)
    if [ -z "${IMAGE_REGISTRY:-}" ]; then
      echo "IMAGE_REGISTRY must be set for push mode" >&2
      exit 1
    fi

    if [ "$IMPLEMENTATION" = "python" ] || [ "$IMPLEMENTATION" = "all" ]; then
      for component in "${python_components[@]}"; do
        image=$(image_name "$component")
        echo "Building and pushing $image"
        build_with_docker "$component"
        docker push "$image"
      done
    fi

    if [ "$IMPLEMENTATION" = "q" ] || [ "$IMPLEMENTATION" = "all" ]; then
      image=$(q_image_name)
      echo "Building and pushing $image"
      build_q_with_docker
      docker push "$image"
    fi
    ;;
  *)
    echo "Usage: $0 [minikube|docker|push] [python|q|all]" >&2
    exit 2
    ;;
esac

echo "Streaming P&L images are ready."
