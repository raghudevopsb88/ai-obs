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


def usage_vs_quota_panel(
    pid: int,
    title: str,
    grid: dict,
    usage_expr: str,
    req_expr: str,
    lim_expr: str,
    unit: str = "short",
) -> dict:
    """Usage is dynamic; requests/limits are instant scalars drawn as flat reference lines."""
    panel = ts_panel(
        pid,
        title,
        grid,
        [
            prom_target(usage_expr, "usage", ref="A"),
            prom_target(f"scalar({req_expr})", "requests", ref="B", instant=True),
            prom_target(f"scalar({lim_expr})", "limits", ref="C", instant=True),
        ],
        unit=unit,
    )
    overrides = [
        {
            "matcher": {"id": "byName", "options": "requests"},
            "properties": [
                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                {"id": "custom.lineWidth", "value": 2},
                {"id": "color", "value": {"fixedColor": "orange", "mode": "fixed"}},
            ],
        },
        {
            "matcher": {"id": "byName", "options": "limits"},
            "properties": [
                {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                {"id": "custom.lineWidth", "value": 2},
                {"id": "color", "value": {"fixedColor": "red", "mode": "fixed"}},
            ],
        },
    ]
    panel["fieldConfig"]["overrides"] = overrides
    return panel


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
    ns = ROBOSHOP_NS
    ctr = 'container!="", container!="POD"'

    cpu_usage_by_pod = (
        f'sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace="{ns}", {pod}, {ctr}}}[5m]))'
    )
    cpu_req_by_pod = (
        f'sum by (pod) (kube_pod_container_resource_requests{{namespace="{ns}", {pod}, resource="cpu", container!=""}})'
    )
    cpu_lim_by_pod = (
        f'sum by (pod) (kube_pod_container_resource_limits{{namespace="{ns}", {pod}, resource="cpu", container!=""}})'
    )
    mem_usage_by_pod = (
        f'sum by (pod) (container_memory_working_set_bytes{{namespace="{ns}", {pod}, {ctr}}})'
    )
    mem_req_by_pod = (
        f'sum by (pod) (kube_pod_container_resource_requests{{namespace="{ns}", {pod}, resource="memory", container!=""}})'
    )
    mem_lim_by_pod = (
        f'sum by (pod) (kube_pod_container_resource_limits{{namespace="{ns}", {pod}, resource="memory", container!=""}})'
    )

    cpu_usage_sum = f'sum(rate(container_cpu_usage_seconds_total{{namespace="{ns}", {pod}, {ctr}}}[5m]))'
    cpu_req_sum = f'sum(kube_pod_container_resource_requests{{namespace="{ns}", {pod}, resource="cpu", container!=""}})'
    cpu_lim_sum = f'sum(kube_pod_container_resource_limits{{namespace="{ns}", {pod}, resource="cpu", container!=""}})'
    mem_usage_sum = f'sum(container_memory_working_set_bytes{{namespace="{ns}", {pod}, {ctr}}})'
    mem_req_sum = f'sum(kube_pod_container_resource_requests{{namespace="{ns}", {pod}, resource="memory", container!=""}})'
    mem_lim_sum = f'sum(kube_pod_container_resource_limits{{namespace="{ns}", {pod}, resource="memory", container!=""}})'

    cpu_pct_req = f"100 * {cpu_usage_sum} / {cpu_req_sum}"
    cpu_pct_lim = f"100 * {cpu_usage_sum} / {cpu_lim_sum}"
    mem_pct_req = f"100 * {mem_usage_sum} / {mem_req_sum}"
    mem_pct_lim = f"100 * {mem_usage_sum} / {mem_lim_sum}"

    cpu_pct_req_by_pod = f"100 * ({cpu_usage_by_pod}) / ({cpu_req_by_pod})"
    cpu_pct_lim_by_pod = f"100 * ({cpu_usage_by_pod}) / ({cpu_lim_by_pod})"
    mem_pct_req_by_pod = f"100 * ({mem_usage_by_pod}) / ({mem_req_by_pod})"
    mem_pct_lim_by_pod = f"100 * ({mem_usage_by_pod}) / ({mem_lim_by_pod})"

    pct_thresholds = [
        {"color": "green", "value": None},
        {"color": "yellow", "value": 70},
        {"color": "orange", "value": 85},
        {"color": "red", "value": 95},
    ]

    panels = [
        stat_panel(1, "Available Replicas", {"x": 0, "y": 0, "w": 4, "h": 4},
                   f'sum(kube_deployment_status_replicas_available{{namespace="{ns}",{dep}}})'),
        stat_panel(2, "Desired Replicas", {"x": 4, "y": 0, "w": 4, "h": 4},
                   f'sum(kube_deployment_spec_replicas{{namespace="{ns}",{dep}}})'),
        stat_panel(3, "CPU % of Request", {"x": 8, "y": 0, "w": 4, "h": 4},
                   cpu_pct_req, unit="percent", decimals=1, thresholds=pct_thresholds),
        stat_panel(4, "CPU % of Limit", {"x": 12, "y": 0, "w": 4, "h": 4},
                   cpu_pct_lim, unit="percent", decimals=1, thresholds=pct_thresholds),
        stat_panel(5, "Memory % of Request", {"x": 16, "y": 0, "w": 4, "h": 4},
                   mem_pct_req, unit="percent", decimals=1, thresholds=pct_thresholds),
        stat_panel(6, "Memory % of Limit", {"x": 20, "y": 0, "w": 4, "h": 4},
                   mem_pct_lim, unit="percent", decimals=1, thresholds=pct_thresholds),

        usage_vs_quota_panel(
            7, "CPU: Usage vs Requests vs Limits", {"x": 0, "y": 4, "w": 24, "h": 8},
            cpu_usage_sum, cpu_req_sum, cpu_lim_sum,
        ),
        ts_panel(8, "CPU Usage by Pod", {"x": 0, "y": 12, "w": 12, "h": 8},
                 [prom_target(cpu_usage_by_pod, "{{pod}}")]),
        ts_panel(9, "CPU Requests & Limits by Pod (static)", {"x": 12, "y": 12, "w": 12, "h": 8}, [
            prom_target(cpu_req_by_pod, "{{pod}} request", instant=True),
            prom_target(cpu_lim_by_pod, "{{pod}} limit", instant=True),
        ]),

        ts_panel(11, "CPU % of Request by Pod", {"x": 0, "y": 20, "w": 12, "h": 8},
                  [prom_target(cpu_pct_req_by_pod, "{{pod}}")], unit="percent", decimals=1),
        ts_panel(12, "CPU % of Limit by Pod", {"x": 12, "y": 20, "w": 12, "h": 8},
                  [prom_target(cpu_pct_lim_by_pod, "{{pod}}")], unit="percent", decimals=1),

        usage_vs_quota_panel(
            13, "Memory: Usage vs Requests vs Limits", {"x": 0, "y": 28, "w": 24, "h": 8},
            mem_usage_sum, mem_req_sum, mem_lim_sum, unit="bytes",
        ),
        ts_panel(14, "Memory Usage by Pod", {"x": 0, "y": 36, "w": 12, "h": 8},
                  [prom_target(mem_usage_by_pod, "{{pod}}")], unit="bytes"),
        ts_panel(15, "Memory Requests & Limits by Pod (static)", {"x": 12, "y": 36, "w": 12, "h": 8}, [
            prom_target(mem_req_by_pod, "{{pod}} request", instant=True),
            prom_target(mem_lim_by_pod, "{{pod}} limit", instant=True),
        ], unit="bytes"),

        ts_panel(17, "Memory % of Request by Pod", {"x": 0, "y": 44, "w": 12, "h": 8},
                  [prom_target(mem_pct_req_by_pod, "{{pod}}")], unit="percent", decimals=1),
        ts_panel(18, "Memory % of Limit by Pod", {"x": 12, "y": 44, "w": 12, "h": 8},
                  [prom_target(mem_pct_lim_by_pod, "{{pod}}")], unit="percent", decimals=1),

        ts_panel(19, "Network I/O by Pod", {"x": 0, "y": 52, "w": 12, "h": 8}, [
            prom_target(f'sum by (pod) (rate(container_network_receive_bytes_total{{namespace="{ns}", {pod}}}[5m]))', "rx {{pod}}", ref="A"),
            prom_target(f'sum by (pod) (rate(container_network_transmit_bytes_total{{namespace="{ns}", {pod}}}[5m]))', "tx {{pod}}", ref="B"),
        ], unit="Bps"),
        ts_panel(20, "Container Restarts", {"x": 12, "y": 52, "w": 12, "h": 8},
                  [prom_target(f'sum by (pod) (kube_pod_container_status_restarts_total{{namespace="{ns}", {pod}}})', "{{pod}}")]),

        stat_panel(21, "Pod Restarts (1h)", {"x": 0, "y": 60, "w": 6, "h": 4},
                   f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{ns}", {pod}}}[1h]))',
                   thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        stat_panel(22, "Traefik req/s", {"x": 6, "y": 60, "w": 6, "h": 4},
                   f'sum(rate(traefik_service_requests_total{{exported_service=~"default-${{deployment}}-.*"}}[5m]))', unit="reqps"),
        ts_panel(23, "Available Replicas Over Time", {"x": 0, "y": 64, "w": 24, "h": 6},
                  [prom_target(f'kube_deployment_status_replicas_available{{namespace="{ns}",{dep}}}', "{{deployment}}")]),
        ts_panel(24, "Traefik Traffic (selected service)", {"x": 0, "y": 70, "w": 12, "h": 8},
                  [prom_target(f'sum by (code, method) (rate(traefik_service_requests_total{{exported_service=~"default-${{deployment}}-.*"}}[5m]))', "{{method}} {{code}}")], unit="reqps", stack=True),
        ts_panel(25, "Traefik P95 Latency (selected service)", {"x": 12, "y": 70, "w": 12, "h": 8},
                  [prom_target(f'histogram_quantile(0.95, sum by (le) (rate(traefik_service_request_duration_seconds_bucket{{exported_service=~"default-${{deployment}}-.*"}}[5m])))', "p95")], unit="s"),
        ts_panel(26, "Traefik Errors (selected service)", {"x": 0, "y": 78, "w": 24, "h": 8},
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
        "description": (
            "Roboshop microservices health. Use Roboshop Service dropdown to filter all panels. "
            "CPU/Memory panels show usage vs Kubernetes requests/limits and % utilization."
        ),
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
