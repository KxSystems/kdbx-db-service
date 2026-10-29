#!/usr/bin/env bash

set -euo pipefail

NAMESPACE=${NAMESPACE:-}
KX_REGISTRY=${KX_REGISTRY:-portal.dl.kx.com}
KX_USER=${KX_USER:-}
KX_PASS=${KX_PASS:-}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if ! command -v kubectl >/dev/null 2>&1; then
  fail "kubectl is required but was not found in PATH."
fi

for variable in NAMESPACE KX_REGISTRY KX_USER KX_PASS; do
  if [ -z "${!variable}" ]; then
    fail "$variable must be set."
  fi
done

CONTEXT=$(kubectl config current-context 2>/dev/null) || \
  fail "No current Kubernetes context is configured."

echo "Using Kubernetes context: $CONTEXT"

echo "Creating or updating namespace: $NAMESPACE"
kubectl create namespace "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Creating or updating image-pull secret: kx-pull-secret"
kubectl create secret docker-registry kx-pull-secret \
  --docker-server="$KX_REGISTRY" \
  --docker-username="$KX_USER" \
  --docker-password="$KX_PASS" \
  -n "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

if [ -n "${LIC_FILE:-}" ]; then
  [ -f "$LIC_FILE" ] || fail "License file does not exist: $LIC_FILE"
  LICENSE_FILE=$LIC_FILE
elif [ -n "${KDB_K4LICENSE_B64:-}" ]; then
  LICENSE_FILE=$(mktemp)
  trap 'rm -f "$LICENSE_FILE"' EXIT
  printf '%s' "$KDB_K4LICENSE_B64" | base64 -d > "$LICENSE_FILE" || \
    fail "KDB_K4LICENSE_B64 could not be decoded."
elif [ -n "${KDB_LICENSE_B64:-}" ]; then
  LICENSE_FILE=$(mktemp)
  trap 'rm -f "$LICENSE_FILE"' EXIT
  printf '%s' "$KDB_LICENSE_B64" | base64 -d > "$LICENSE_FILE" || \
    fail "KDB_LICENSE_B64 could not be decoded."
else
  fail "Set LIC_FILE, KDB_K4LICENSE_B64, or KDB_LICENSE_B64 before continuing."
fi

echo "Creating or updating license secret: kx-license"
kubectl create secret generic kx-license \
  --from-file="license=$LICENSE_FILE" \
  -n "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl get secret kx-pull-secret kx-license -n "$NAMESPACE"

echo "Namespace and secrets are ready."
