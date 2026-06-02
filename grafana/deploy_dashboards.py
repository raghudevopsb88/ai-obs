#!/usr/bin/env python3
"""Deploy Roboshop + Traefik Grafana dashboards via API."""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from base64 import b64encode

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ["GRAFANA_PASSWORD"]
FOLDER_UID = os.environ.get("GRAFANA_FOLDER_UID", "roboshop-obs")
FOLDER_TITLE = os.environ.get("GRAFANA_FOLDER_TITLE", "Roboshop Observability")
DS_UID = os.environ.get("PROMETHEUS_DS_UID", "prometheus")
ROBOSHOP_NS = os.environ.get("ROBOSHOP_NAMESPACE", "default")

DS = {"type": "prometheus", "uid": DS_UID}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(method: str, path: str, data: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"{GRAFANA_URL}{path}", data=body, method=method)
    req.add_header("Content-Type", "application/json")
    token = b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"Grafana API {method} {path} failed ({exc.code}): {detail[:500]}") from exc


def prom_target(expr: str, legend: str = "", instant: bool = False, ref: str = "A") -> dict:
    return {
        "datasource": DS,
        "editorMode": "code",
        "expr": expr,
        "legendFormat": legend,
        "range": not instant,
        "instant": instant,
        "refId": ref,
    }


def ts_panel(
    pid: int,
    title: str,
    grid: dict,
    targets: list,
    unit: str = "short",
    stack: bool = False,
    decimals: int | None = None,
) -> dict:
    defaults: dict = {
        "color": {"mode": "palette-classic"},
        "custom": {
            "drawStyle": "line",
            "lineWidth": 1,
            "fillOpacity": 15 if stack else 10,
            "showPoints": "never",
            "spanNulls": True,
            "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        },
        "unit": unit,
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "gridPos": grid,
        "datasource": DS,
        "pluginVersion": "11.5.0",
        "fieldConfig": {
            "defaults": defaults,
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"], "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": targets,
    }


def stat_panel(
    pid: int,
    title: str,
    grid: dict,
    expr: str,
    unit: str = "short",
    thresholds: list | None = None,
    decimals: int | None = None,
    instant: bool = False,
) -> dict:
    steps = thresholds or [{"color": "green", "value": None}]
    defaults: dict = {
        "unit": unit,
        "thresholds": {"mode": "absolute", "steps": steps},
        "color": {"mode": "thresholds"},
    }
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "gridPos": grid,
        "datasource": DS,
        "pluginVersion": "11.5.0",
        "fieldConfig": {
            "defaults": defaults,
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "textMode": "auto",
        },
        "targets": [prom_target(expr, instant=instant)],
    }


def pie_panel(pid: int, title: str, grid: dict, expr: str, legend: str = "{{code}}") -> dict:
    return {
        "id": pid,
        "type": "piechart",
        "title": title,
        "gridPos": grid,
        "datasource": DS,
        "pluginVersion": "11.5.0",
        "fieldConfig": {
            "defaults": {"unit": "none", "decimals": 0},
            "overrides": [],
        },
        "targets": [prom_target(expr, legend=legend, instant=True)],
        "options": {
            "legend": {"displayMode": "table", "placement": "right", "showLegend": True},
            "pieType": "donut",
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
    }


def datasource_var() -> dict:
    return {
        "name": "datasource",
        "type": "datasource",
        "query": "prometheus",
        "current": {"selected": True, "text": "Prometheus", "value": DS_UID},
        "hide": 0,
        "includeAll": False,
        "multi": False,
        "options": [],
        "refresh": 1,
        "regex": "",
        "skipUrlSync": False,
    }


def query_var(name: str, query: str, label: str, multi: bool = True, include_all: bool = True) -> dict:
    return {
        "name": name,
        "type": "query",
        "datasource": DS,
        "definition": query,
        "query": {"qryType": 1, "query": query, "refId": "VariableQuery"},
        "label": label,
        "hide": 0,
        "includeAll": include_all,
        "multi": multi,
        "allValue": ".*",
        "current": {"selected": True, "text": "All", "value": "$__all"},
        "options": [],
        "refresh": 2,
        "regex": "",
        "skipUrlSync": False,
        "sort": 1,
    }


def ensure_folder() -> None:
    try:
        api("GET", f"/api/folders/{FOLDER_UID}")
    except RuntimeError:
        api("POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE})
        print(f"Created Grafana folder: {FOLDER_TITLE}")


def upsert_dashboard(dashboard: dict) -> None:
    uid = dashboard["uid"]
    try:
        existing = api("GET", f"/api/dashboards/uid/{uid}")
        dashboard["id"] = existing["dashboard"]["id"]
        dashboard["version"] = existing["dashboard"]["version"]
    except RuntimeError:
        pass
    result = api(
        "POST",
        "/api/dashboards/db",
        {"dashboard": dashboard, "overwrite": True, "folderUid": FOLDER_UID},
    )
    print(f"  {dashboard['title']}: {result.get('url', result)}")


def build_traefik_dashboard() -> dict:
    svc = 'exported_service=~"$service"'
    ep = 'entrypoint=~"$entrypoint"'
    err = f'{svc},code=~"4..|5.."'

    # Cumulative counters match `kubectl logs ... | grep 503 | wc -l` on the current Traefik pod.
    # increase() under-counts burst errors (~50%) due to Prometheus scrape extrapolation.
    req_total = f"sum(traefik_service_requests_total{{{svc}}})"
    err_total = f"sum(traefik_service_requests_total{{{err}}})"
    req_delta = (
        f"sum(max_over_time(traefik_service_requests_total{{{svc}}}[5m]) "
        f"- min_over_time(traefik_service_requests_total{{{svc}}}[5m]))"
    )
    err_delta = (
        f"sum by (code) (max_over_time(traefik_service_requests_total{{{err}}}[5m]) "
        f"- min_over_time(traefik_service_requests_total{{{err}}}[5m]))"
    )
    req_delta_by_code = (
        f"sum by (code) (max_over_time(traefik_service_requests_total{{{svc}}}[5m]) "
        f"- min_over_time(traefik_service_requests_total{{{svc}}}[5m]))"
    )
    status_pie = (
        f"sum by (code) (max_over_time(traefik_service_requests_total{{{svc}}}[$__range]) "
        f"- min_over_time(traefik_service_requests_total{{{svc}}}[$__range]))"
    )

    panels = [
        stat_panel(1, "Request Rate", {"x": 0, "y": 0, "w": 4, "h": 4},
                   f"sum(rate(traefik_service_requests_total{{{svc}}}[5m]))", unit="reqps"),
        stat_panel(2, "Total Requests", {"x": 4, "y": 0, "w": 4, "h": 4},
                   req_total, unit="none", decimals=0, instant=True),
        stat_panel(3, "Error Rate (4xx/5xx)", {"x": 8, "y": 0, "w": 4, "h": 4},
                   f"sum(rate(traefik_service_requests_total{{{err}}}[5m]))", unit="reqps",
                   thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 10}]),
        stat_panel(4, "Total Errors", {"x": 12, "y": 0, "w": 4, "h": 4},
                   err_total, unit="none", decimals=0, instant=True,
                   thresholds=[{"color": "green", "value": None}, {"color": "yellow", "value": 1}, {"color": "red", "value": 10}]),
        stat_panel(5, "P95 Latency", {"x": 16, "y": 0, "w": 4, "h": 4},
                   f"histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{{svc}}}[5m])))", unit="s"),
        stat_panel(6, "Open Connections", {"x": 20, "y": 0, "w": 4, "h": 4},
                   f"sum(traefik_open_connections{{{ep}}})"),
        ts_panel(7, "Request Rate Over Time", {"x": 0, "y": 4, "w": 12, "h": 8},
                 [prom_target(f"sum(rate(traefik_service_requests_total{{{svc}}}[5m]))", "req/s")], unit="reqps"),
        ts_panel(8, "Request Count Over Time", {"x": 12, "y": 4, "w": 12, "h": 8},
                 [prom_target(req_delta, "requests / 5m")], unit="none", decimals=0),
        ts_panel(9, "Request Rate by Status Code", {"x": 0, "y": 12, "w": 12, "h": 8},
                 [prom_target(f"sum by (code) (rate(traefik_service_requests_total{{{svc}}}[5m]))", "{{code}}")], unit="reqps", stack=True),
        ts_panel(10, "Request Count by Status Code", {"x": 12, "y": 12, "w": 12, "h": 8},
                  [prom_target(req_delta_by_code, "{{code}}")], unit="none", decimals=0, stack=True),
        ts_panel(11, "Latency Percentiles", {"x": 0, "y": 20, "w": 12, "h": 8}, [
            prom_target(f"histogram_quantile(0.50, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{{svc}}}[5m])))", "p50", ref="A"),
            prom_target(f"histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{{svc}}}[5m])))", "p95", ref="B"),
            prom_target(f"histogram_quantile(0.99, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{{svc}}}[5m])))", "p99", ref="C"),
        ], unit="s"),
        ts_panel(12, "Bandwidth (Service)", {"x": 12, "y": 20, "w": 12, "h": 8}, [
            prom_target(f"sum(rate(traefik_service_requests_bytes_total{{{svc}}}[5m]))", "request B/s", ref="A"),
            prom_target(f"sum(rate(traefik_service_responses_bytes_total{{{svc}}}[5m]))", "response B/s", ref="B"),
        ], unit="Bps"),
        ts_panel(13, "Error Rate Over Time", {"x": 0, "y": 28, "w": 12, "h": 8},
                 [prom_target(f'sum by (code) (rate(traefik_service_requests_total{{{err}}}[5m]))', "{{code}}")], unit="reqps", stack=True),
        ts_panel(14, "Error Count Over Time", {"x": 12, "y": 28, "w": 12, "h": 8},
                  [prom_target(err_delta, "{{code}}")], unit="none", decimals=0, stack=True),
        pie_panel(15, "Status Code Distribution", {"x": 0, "y": 36, "w": 12, "h": 8},
                  status_pie, legend="{{code}}"),
        ts_panel(16, "Entrypoint Request Rate", {"x": 12, "y": 36, "w": 12, "h": 8},
                 [prom_target(f"sum by (entrypoint) (rate(traefik_entrypoint_requests_total{{{ep}}}[5m]))", "{{entrypoint}}")], unit="reqps"),
        ts_panel(17, "Entrypoint P95 Latency", {"x": 0, "y": 44, "w": 12, "h": 8},
                 [prom_target(f"histogram_quantile(0.95, sum by (le, entrypoint) (rate(traefik_entrypoint_request_duration_seconds_bucket{{{ep}}}[5m])))", "{{entrypoint}}")], unit="s"),
        ts_panel(18, "Request Rate by Method", {"x": 12, "y": 44, "w": 12, "h": 8},
                 [prom_target(f"sum by (method) (rate(traefik_service_requests_total{{{svc}}}[5m]))", "{{method}}")], unit="reqps", stack=True),
    ]

    variables = [
        datasource_var(),
        query_var("service", "label_values(traefik_service_requests_total, exported_service)", "Backend Service"),
        query_var("entrypoint", "label_values(traefik_entrypoint_requests_total, entrypoint)", "Entrypoint"),
    ]

    return {
        "uid": "traefik-overview",
        "title": "Traefik - Ingress Overview",
        "tags": ["traefik", "ingress"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "description": (
            "Traefik ingress metrics. Use Backend Service dropdown to filter by routed service. "
            "Total Requests / Total Errors are cumulative counters since the Traefik pod last restarted "
            "(matches traefik access log line counts). Rate panels use per-second averages."
        ),
        "panels": panels,
        "templating": {"list": variables},
        "annotations": {"list": []},
        "links": [],
    }


def build_roboshop_dashboard() -> dict:
    dep = 'deployment=~"$deployment"'
    pod = 'pod=~"${deployment}-.*"'

    panels = [
        stat_panel(1, "Available Replicas", {"x": 0, "y": 0, "w": 6, "h": 4},
                   f'sum(kube_deployment_status_replicas_available{{namespace="{ROBOSHOP_NS}",{dep}}})'),
        stat_panel(2, "Desired Replicas", {"x": 6, "y": 0, "w": 6, "h": 4},
                   f'sum(kube_deployment_spec_replicas{{namespace="{ROBOSHOP_NS}",{dep}}})'),
        stat_panel(3, "Pod Restarts (1h)", {"x": 12, "y": 0, "w": 6, "h": 4},
                   f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ROBOSHOP_NS}",{pod}}}[1h]))',
                   thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        stat_panel(4, "Traefik req/s", {"x": 18, "y": 0, "w": 6, "h": 4},
                   f'sum(rate(traefik_service_requests_total{{exported_service=~"default-${{deployment}}-.*"}}[5m]))', unit="reqps"),
        ts_panel(5, "Available Replicas Over Time", {"x": 0, "y": 4, "w": 12, "h": 8},
                 [prom_target(f'kube_deployment_status_replicas_available{{namespace="{ROBOSHOP_NS}",{dep}}}', "{{deployment}}")]),
        ts_panel(6, "CPU Usage by Pod", {"x": 12, "y": 4, "w": 12, "h": 8},
                 [prom_target(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{ROBOSHOP_NS}",{pod},container!="",container!="POD"}}[5m]))', "{{pod}}")]),
        ts_panel(7, "Memory Usage by Pod", {"x": 0, "y": 12, "w": 12, "h": 8},
                 [prom_target(f'sum by (pod) (container_memory_working_set_bytes{{namespace="{ROBOSHOP_NS}",{pod},container!="",container!="POD"}})', "{{pod}}")], unit="bytes"),
        ts_panel(8, "Network I/O by Pod", {"x": 12, "y": 12, "w": 12, "h": 8}, [
            prom_target(f'sum by (pod) (rate(container_network_receive_bytes_total{{namespace="{ROBOSHOP_NS}",{pod}}}[5m]))', "rx {{pod}}", ref="A"),
            prom_target(f'sum by (pod) (rate(container_network_transmit_bytes_total{{namespace="{ROBOSHOP_NS}",{pod}}}[5m]))', "tx {{pod}}", ref="B"),
        ], unit="Bps"),
        ts_panel(9, "Container Restarts", {"x": 0, "y": 20, "w": 12, "h": 8},
                 [prom_target(f'sum by (pod) (kube_pod_container_status_restarts_total{{namespace="{ROBOSHOP_NS}",{pod}}})', "{{pod}}")]),
        ts_panel(10, "Traefik Traffic (selected service)", {"x": 12, "y": 20, "w": 12, "h": 8},
                  [prom_target(f'sum by (code, method) (rate(traefik_service_requests_total{{exported_service=~"default-${{deployment}}-.*"}}[5m]))', "{{method}} {{code}}")], unit="reqps", stack=True),
        ts_panel(11, "Traefik P95 Latency (selected service)", {"x": 0, "y": 28, "w": 12, "h": 8},
                 [prom_target(f'histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{exported_service=~"default-${{deployment}}-.*"}}[5m])))', "p95")], unit="s"),
        ts_panel(12, "Traefik Errors (selected service)", {"x": 12, "y": 28, "w": 12, "h": 8},
                 [prom_target(f'sum by (code) (rate(traefik_service_requests_total{{exported_service=~"default-${{deployment}}-.*",code=~"4..|5.."}}[5m]))', "{{code}}")], unit="reqps", stack=True),
    ]

    variables = [
        datasource_var(),
        query_var(
            "deployment",
            f'label_values(kube_deployment_status_replicas_available{{namespace="{ROBOSHOP_NS}", deployment=~"roboshop-.*"}}, deployment)',
            "Roboshop Service",
        ),
    ]

    return {
        "uid": "roboshop-cluster",
        "title": "Roboshop - Cluster Health",
        "tags": ["roboshop", "kubernetes"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "description": "Roboshop microservices health. Use Roboshop Service dropdown to filter all panels.",
        "panels": panels,
        "templating": {"list": variables},
        "annotations": {"list": []},
        "links": [],
    }


def main() -> int:
    if not GRAFANA_PASSWORD:
        print("GRAFANA_PASSWORD is required", file=sys.stderr)
        return 1

    print(f"Grafana: {GRAFANA_URL}")
    ensure_folder()
    print("Deploying dashboards:")
    upsert_dashboard(build_traefik_dashboard())
    upsert_dashboard(build_roboshop_dashboard())
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
