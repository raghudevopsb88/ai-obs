#!/usr/bin/env bash
# Configure kube-prometheus-stack Grafana to send mail via SMTP (e.g. AWS SES).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT_DIR}/config.env" ]]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/config.env"
fi

NS="${MONITORING_NAMESPACE:-monitoring}"
DEPLOY="${GRAFANA_DEPLOYMENT:-kube-prometheus-stack-grafana}"
SECRET="${GRAFANA_SMTP_SECRET:-grafana-smtp-config}"
FROM="${GRAFANA_SMTP_FROM:-}"
FROM_NAME="${GRAFANA_SMTP_FROM_NAME:-Roboshop Observability}"

require_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing ${name} (required for Grafana email alerts)" >&2
    exit 1
  fi
}

require_var GRAFANA_SMTP_HOST
require_var GRAFANA_SMTP_USER
require_var GRAFANA_SMTP_PASSWORD
require_var GRAFANA_SMTP_FROM

if ! kubectl get deployment "$DEPLOY" -n "$NS" >/dev/null 2>&1; then
  echo "Grafana deployment ${DEPLOY} not found in namespace ${NS}" >&2
  exit 1
fi

CONTAINER="${GRAFANA_CONTAINER:-grafana}"
if ! kubectl get deployment "$DEPLOY" -n "$NS" \
  -o "jsonpath={.spec.template.spec.containers[?(@.name=='${CONTAINER}')].name}" | grep -qx "$CONTAINER"; then
  echo "Container '${CONTAINER}' not found in deployment/${DEPLOY}. Available containers:" >&2
  kubectl get deployment "$DEPLOY" -n "$NS" \
    -o jsonpath='{range .spec.template.spec.containers[*]}{.name}{"\n"}{end}' >&2
  exit 1
fi

echo "Configuring Grafana SMTP on deployment/${DEPLOY} (container: ${CONTAINER})"

kubectl create secret generic "$SECRET" -n "$NS" \
  --from-literal=GF_SMTP_USER="$GRAFANA_SMTP_USER" \
  --from-literal=GF_SMTP_PASSWORD="$GRAFANA_SMTP_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl set env "deployment/$DEPLOY" -n "$NS" \
  --containers="$CONTAINER" \
  GF_SMTP_ENABLED=true \
  GF_SMTP_HOST="$GRAFANA_SMTP_HOST" \
  GF_SMTP_FROM_ADDRESS="$FROM" \
  GF_SMTP_FROM_NAME="$FROM_NAME" \
  GF_SMTP_STARTTLS_POLICY=Mandatory \
  --from="secret/$SECRET"

echo "Waiting for Grafana rollout after SMTP update..."
kubectl rollout status "deployment/$DEPLOY" -n "$NS" --timeout=180s
echo "Grafana SMTP is configured (from: ${FROM})"
