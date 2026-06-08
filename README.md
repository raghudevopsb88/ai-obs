# Roboshop Observability

Repeatable deploy for Grafana dashboards and Traefik Prometheus scraping on a fresh EKS cluster.

Run this after:
- EKS cluster is up
- `kube-prometheus-stack` is installed in `monitoring`
- Traefik is installed in `traefik`
- Roboshop workloads are deployed in `default`

## Quick start

```bash
# From a host with kubectl access (e.g. your EKS bastion)
git clone <this-repo>
cd ai-obs

cp config.env.example config.env
# optional: set GRAFANA_URL=https://grafana-dev.example.com

chmod +x deploy.sh
./deploy.sh
```

## What it deploys

| Resource | Purpose |
|----------|---------|
| `kubernetes/monitoring/traefik-metrics-service.yaml` | Exposes Traefik `:9100` metrics port |
| `kubernetes/monitoring/traefik-servicemonitor.yaml` | Tells Prometheus to scrape Traefik |
| `grafana/deploy_dashboards.py` | Creates/updates Grafana dashboards via API |
| `grafana/deploy_alerts.py` | Creates/updates Grafana alert rules + email routing via API |
| `scripts/configure_grafana_smtp.sh` | Configures Grafana pod SMTP (AWS SES) via kubectl |

## Dashboards

Folder: **Roboshop Observability**

- **Traefik - Ingress Overview** (`/d/traefik-overview`)
  - Traffic, errors, latency, bandwidth
  - Variables: Backend Service, Entrypoint
- **Roboshop - Cluster Health** (`/d/roboshop-cluster`)
  - Replicas, CPU, memory, network, restarts
  - Variable: Roboshop Service

## Alerts

Deployed to folder **Roboshop Observability** (Grafana → Alerting → Alert rules):

| Group | Rule | Fires when |
|-------|------|------------|
| **traefik-alerts** | Ingress errors (4xx/5xx) | Error rate &gt; 0 for 2m |
| | Ingress 5xx | 5xx rate &gt; 0 for 1m (critical) |
| | High P95 latency | P95 &gt; 2s for 5m |
| **roboshop-alerts** | Service HTTP errors | Any roboshop backend returns 4xx/5xx |
| | Replica mismatch | Available &lt; desired for 5m |
| | Pod restart | Container restart in last 15m |
| | CPU near limit | Pod CPU &gt; 85% of limit for 5m |
| | Memory near limit | Pod memory &gt; 85% of limit for 5m |

Alerts link back to the matching dashboard panel.

### Email notifications (AWS SES)

When SMTP variables are set in `config.env`, `./deploy.sh` will:

1. Patch the Grafana pod with SES SMTP settings (`scripts/configure_grafana_smtp.sh`)
2. Create an email contact point and set the default notification policy
3. Deploy alert rules

Example `config.env` (use your SES **SMTP** credentials from AWS console → SES → SMTP settings):

```bash
GRAFANA_SMTP_HOST=email-smtp.us-east-1.amazonaws.com:587
GRAFANA_SMTP_USER=AKIA...            # SES SMTP username (not IAM access key)
GRAFANA_SMTP_PASSWORD=...            # SES SMTP password
GRAFANA_SMTP_FROM=raghudevops88@gmail.com
GRAFANA_ALERT_EMAIL=raghudevops88@gmail.com
```

`GRAFANA_SMTP_FROM` must be a verified sender in SES. If `GRAFANA_ALERT_EMAIL` is omitted, it defaults to `GRAFANA_SMTP_FROM`.

Thresholds are configurable in `config.env` (`ALERT_CPU_LIMIT_PCT`, `ALERT_MEM_LIMIT_PCT`, etc.).

Redeploy alerts only:

```bash
export GRAFANA_URL=https://grafana-dev.example.com
export GRAFANA_PASSWORD=...
python3 grafana/deploy_alerts.py
```

## Configuration

Copy `config.env.example` to `config.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_URL` | _(port-forward)_ | Grafana ingress URL; leave empty to auto port-forward |
| `GRAFANA_PASSWORD` | _(from secret)_ | Override admin password |
| `MONITORING_NAMESPACE` | `monitoring` | Namespace of kube-prometheus-stack |
| `ROBOSHOP_NAMESPACE` | `default` | Namespace where roboshop runs |
| `WAIT_FOR_TRAEFIK_METRICS` | `120` | Seconds to wait for Traefik metrics |
| `ALERT_CPU_LIMIT_PCT` | `85` | Fire when pod CPU exceeds this % of limit |
| `ALERT_MEM_LIMIT_PCT` | `85` | Fire when pod memory exceeds this % of limit |
| `ALERT_LATENCY_P95_SEC` | `2` | Traefik P95 latency alert threshold (seconds) |
| `ALERT_ERROR_RATE_MIN` | `0.001` | Minimum error rate (req/s) to treat as an error |
| `GRAFANA_SMTP_HOST` | _(empty)_ | AWS SES SMTP endpoint, e.g. `email-smtp.us-east-1.amazonaws.com:587` |
| `GRAFANA_SMTP_USER` | _(empty)_ | SES SMTP username |
| `GRAFANA_SMTP_PASSWORD` | _(empty)_ | SES SMTP password |
| `GRAFANA_SMTP_FROM` | `raghudevops88@gmail.com` | Verified SES sender / From address |
| `GRAFANA_ALERT_EMAIL` | `raghudevops88@gmail.com` | Alert notification recipient |
| `GRAFANA_DEPLOYMENT` | `kube-prometheus-stack-grafana` | Grafana Deployment name in cluster |

## Daily / cluster recreate workflow

```bash
# 1. Recreate cluster and deploy apps (your existing process)
# 2. Re-run observability deploy
./deploy.sh
```

Grafana admin password is read automatically from:

```bash
kubectl get secret -n monitoring kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d
```

## Notes

- Traefik backend names appear as `exported_service` in Prometheus (not `service`) because of Kubernetes label collision during scrape.
- Dashboards may show no data until Traefik receives traffic and Prometheus completes at least one scrape (~30s).
- **Total Requests / Total Errors** use cumulative Prometheus counters (same source as Traefik metrics), so they match `kubectl logs ... | grep 503 | wc -l` on the current Traefik pod. Do not use `increase()` for these — it under-counts burst errors between scrapes.
- Filter **Backend Service** to `default-roboshop-frontend-8080@kubernetes` for payment/API errors routed through the frontend ingress.
- Idempotent: safe to run `./deploy.sh` multiple times.

## Troubleshooting

```bash
# Check Traefik scrape target
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
curl -sG 'http://127.0.0.1:9090/api/v1/query' \
  --data-urlencode 'query=up{job="traefik-metrics"}'

# Redeploy dashboards only
export GRAFANA_PASSWORD=$(kubectl get secret -n monitoring kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d)
kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80 &
export GRAFANA_URL=http://127.0.0.1:3000
python3 grafana/deploy_dashboards.py
```
