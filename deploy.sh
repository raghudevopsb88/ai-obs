#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ -f config.env ]]; then
  # shellcheck disable=SC1091
  source config.env
fi

MONITORING_NAMESPACE="${MONITORING_NAMESPACE:-monitoring}"
TRAEFIK_NAMESPACE="${TRAEFIK_NAMESPACE:-traefik}"
GRAFANA_SECRET="${GRAFANA_SECRET:-kube-prometheus-stack-grafana}"
GRAFANA_SERVICE="${GRAFANA_SERVICE:-kube-prometheus-stack-grafana}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_URL="${GRAFANA_URL:-}"
WAIT_FOR_TRAEFIK_METRICS="${WAIT_FOR_TRAEFIK_METRICS:-120}"
PORT_FORWARD_PID=""

cleanup() {
  if [[ -n "$PORT_FORWARD_PID" ]] && kill -0 "$PORT_FORWARD_PID" 2>/dev/null; then
    kill "$PORT_FORWARD_PID" 2>/dev/null || true
    wait "$PORT_FORWARD_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

wait_for_grafana() {
  local url="$1"
  local attempt
  for attempt in $(seq 1 60); do
    if curl -sf "$url/api/health" >/dev/null 2>&1; then
      echo "Grafana is ready"
      return 0
    fi
    sleep 5
  done
  echo "Timed out waiting for Grafana at $url" >&2
  exit 1
}

wait_for_traefik_metrics() {
  local attempt max="$WAIT_FOR_TRAEFIK_METRICS"
  echo "Waiting up to ${max}s for Traefik metrics in Prometheus..."
  kubectl port-forward -n "$MONITORING_NAMESPACE" "svc/kube-prometheus-stack-prometheus" 9090:9090 >/dev/null 2>&1 &
  local pf=$!
  sleep 3
  for attempt in $(seq 1 "$max"); do
    if curl -sfG "http://127.0.0.1:9090/api/v1/query" \
      --data-urlencode 'query=count(traefik_entrypoint_requests_total)' \
      | grep -q '"value":\['; then
      kill "$pf" 2>/dev/null || true
      wait "$pf" 2>/dev/null || true
      echo "Traefik metrics are available in Prometheus"
      return 0
    fi
    sleep 1
  done
  kill "$pf" 2>/dev/null || true
  wait "$pf" 2>/dev/null || true
  echo "Warning: Traefik metrics not found yet. Dashboards will deploy but may be empty until traffic/metrics appear."
}

echo "=== Roboshop observability deploy ==="

require_cmd kubectl
require_cmd python3
require_cmd curl

echo "Step 1/6: Apply Kubernetes monitoring manifests"
kubectl apply -f kubernetes/monitoring/traefik-metrics-service.yaml
kubectl apply -f kubernetes/monitoring/traefik-servicemonitor.yaml

echo "Step 2/6: Wait for Prometheus to scrape Traefik"
wait_for_traefik_metrics

echo "Step 3/6: Resolve Grafana credentials"
if [[ -z "${GRAFANA_PASSWORD:-}" ]]; then
  GRAFANA_PASSWORD="$(kubectl get secret -n "$MONITORING_NAMESPACE" "$GRAFANA_SECRET" -o jsonpath='{.data.admin-password}' | base64 -d)"
fi
export GRAFANA_PASSWORD
export GRAFANA_USER
export PROMETHEUS_DS_UID="${PROMETHEUS_DS_UID:-prometheus}"
export GRAFANA_FOLDER_UID="${GRAFANA_FOLDER_UID:-roboshop-obs}"
export GRAFANA_FOLDER_TITLE="${GRAFANA_FOLDER_TITLE:-Roboshop Observability}"
export ROBOSHOP_NAMESPACE="${ROBOSHOP_NAMESPACE:-default}"
export ALERT_CPU_LIMIT_PCT="${ALERT_CPU_LIMIT_PCT:-85}"
export ALERT_MEM_LIMIT_PCT="${ALERT_MEM_LIMIT_PCT:-85}"
export ALERT_LATENCY_P95_SEC="${ALERT_LATENCY_P95_SEC:-2}"
export ALERT_ERROR_RATE_MIN="${ALERT_ERROR_RATE_MIN:-0.001}"
export ALERT_RULE_INTERVAL="${ALERT_RULE_INTERVAL:-60}"
export GRAFANA_ALERT_EMAIL="${GRAFANA_ALERT_EMAIL:-}"
export GRAFANA_SMTP_FROM="${GRAFANA_SMTP_FROM:-}"
export GRAFANA_SMTP_FROM_NAME="${GRAFANA_SMTP_FROM_NAME:-Roboshop Observability}"
export GRAFANA_DEPLOYMENT="${GRAFANA_DEPLOYMENT:-kube-prometheus-stack-grafana}"

if [[ -n "${GRAFANA_SMTP_HOST:-}" ]]; then
  echo "Step 4/6: Configure Grafana SMTP (AWS SES)"
  bash scripts/configure_grafana_smtp.sh
else
  echo "Step 4/6: Skipping Grafana SMTP (set GRAFANA_SMTP_* in config.env for email alerts)"
fi

echo "Step 5/6: Deploy Grafana dashboards"
if [[ -z "$GRAFANA_URL" ]]; then
  kubectl port-forward -n "$MONITORING_NAMESPACE" "svc/$GRAFANA_SERVICE" 3000:80 >/dev/null 2>&1 &
  PORT_FORWARD_PID=$!
  export GRAFANA_URL="http://127.0.0.1:3000"
  sleep 3
fi

if [[ -z "${GRAFANA_ALERT_EMAIL:-}" && -n "${GRAFANA_SMTP_FROM:-}" ]]; then
  export GRAFANA_ALERT_EMAIL="$GRAFANA_SMTP_FROM"
fi

wait_for_grafana "$GRAFANA_URL"
python3 grafana/deploy_dashboards.py

echo "Step 6/6: Deploy Grafana alert rules and email routing"
python3 grafana/deploy_alerts.py

echo ""
echo "Dashboards deployed to folder: ${GRAFANA_FOLDER_TITLE}"
echo "  /d/traefik-overview/traefik-ingress-overview"
echo "  /d/roboshop-cluster/roboshop-cluster-health"
echo "Alert rules: Grafana → Alerting → Alert rules (folder: ${GRAFANA_FOLDER_TITLE})"
if [[ -n "${GRAFANA_ALERT_EMAIL:-}" && -n "${GRAFANA_SMTP_HOST:-}" ]]; then
  echo "Alert emails → ${GRAFANA_ALERT_EMAIL} (via ${GRAFANA_SMTP_FROM})"
elif [[ -n "${GRAFANA_ALERT_EMAIL:-}" ]]; then
  echo "Warning: GRAFANA_ALERT_EMAIL is set but GRAFANA_SMTP_HOST is not — emails will not send until SMTP is configured."
fi
if [[ -z "${GRAFANA_URL:-}" || "$GRAFANA_URL" == "http://127.0.0.1:3000" ]]; then
  echo ""
  echo "Tip: set GRAFANA_URL in config.env to your ingress URL to skip port-forward."
fi
