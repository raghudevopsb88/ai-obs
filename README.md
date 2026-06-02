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

## Dashboards

Folder: **Roboshop Observability**

- **Traefik - Ingress Overview** (`/d/traefik-overview`)
  - Traffic, errors, latency, bandwidth
  - Variables: Backend Service, Entrypoint
- **Roboshop - Cluster Health** (`/d/roboshop-cluster`)
  - Replicas, CPU, memory, network, restarts
  - Variable: Roboshop Service

## Configuration

Copy `config.env.example` to `config.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAFANA_URL` | _(port-forward)_ | Grafana ingress URL; leave empty to auto port-forward |
| `GRAFANA_PASSWORD` | _(from secret)_ | Override admin password |
| `MONITORING_NAMESPACE` | `monitoring` | Namespace of kube-prometheus-stack |
| `ROBOSHOP_NAMESPACE` | `default` | Namespace where roboshop runs |
| `WAIT_FOR_TRAEFIK_METRICS` | `120` | Seconds to wait for Traefik metrics |

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
