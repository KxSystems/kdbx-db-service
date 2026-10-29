#!/usr/bin/env bash

set -euo pipefail

PROFILE=${MINIKUBE_PROFILE:-db-service-refarch}
MINIKUBE_CPUS=${MINIKUBE_CPUS:-8}
MINIKUBE_MEMORY=${MINIKUBE_MEMORY:-16g}
MINIKUBE_DISK_SIZE=${MINIKUBE_DISK_SIZE:-50g}

for tool in minikube kubectl; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: $tool is required but was not found in PATH." >&2
    exit 1
  fi
done

if ! minikube status -p "$PROFILE" >/dev/null 2>&1; then
  echo "Starting Minikube profile '$PROFILE'..."
  minikube start \
    -p "$PROFILE" \
    --driver=docker \
    --cpus="$MINIKUBE_CPUS" \
    --memory="$MINIKUBE_MEMORY" \
    --disk-size="$MINIKUBE_DISK_SIZE"
fi

if ! kubectl --context "$PROFILE" get storageclass standard >/dev/null 2>&1; then
  echo "ERROR: Minikube storage class 'standard' is not available." >&2
  exit 1
fi

kubectl config use-context "$PROFILE" >/dev/null

cat <<EOF

Minikube profile '$PROFILE' is ready.

Continue with the README's "Deploy the chart" section to deploy DB Service.
EOF
