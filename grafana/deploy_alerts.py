#!/usr/bin/env python3
"""Deploy Grafana unified alerting rules for Roboshop + Traefik dashboards."""

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
EXPR_DS = "__expr__"

ALERT_EMAIL = os.environ.get("GRAFANA_ALERT_EMAIL", "").strip()
CONTACT_UID = os.environ.get("GRAFANA_ALERT_CONTACT_UID", "roboshop-email")

CPU_LIMIT_PCT = float(os.environ.get("ALERT_CPU_LIMIT_PCT", "85"))
MEM_LIMIT_PCT = float(os.environ.get("ALERT_MEM_LIMIT_PCT", "85"))
LATENCY_P95_SEC = float(os.environ.get("ALERT_LATENCY_P95_SEC", "2"))
ERROR_RATE_MIN = float(os.environ.get("ALERT_ERROR_RATE_MIN", "0.001"))
RULE_INTERVAL = int(os.environ.get("ALERT_RULE_INTERVAL", "60"))

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api(method: str, path: str, data: dict | None = None, extra_headers: dict | None = None) -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(f"{GRAFANA_URL}{path}", data=body, method=method)
    req.add_header("Content-Type", "application/json")
    token = b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if extra_headers:
        for key, value in extra_headers.items():
            req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"Grafana API {method} {path} failed ({exc.code}): {detail[:800]}") from exc


def ensure_folder() -> None:
    try:
        api("GET", f"/api/folders/{FOLDER_UID}")
    except RuntimeError:
        api("POST", "/api/folders", {"uid": FOLDER_UID, "title": FOLDER_TITLE})


def prom_alert_query(ref: str, expr: str, relative_from: int = 600) -> dict:
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": relative_from, "to": 0},
        "datasourceUid": DS_UID,
        "model": {
            "editorMode": "code",
            "expr": expr,
            "instant": True,
            "intervalMs": 1000,
            "legendFormat": "",
            "maxDataPoints": 43200,
            "range": False,
            "refId": ref,
            "datasource": {"type": "prometheus", "uid": DS_UID},
        },
    }


def reduce_query(ref: str, source: str, reducer: str = "last") -> dict:
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": 0, "to": 0},
        "datasourceUid": EXPR_DS,
        "model": {
            "datasource": {"type": "__expr__", "uid": EXPR_DS},
            "expression": source,
            "hide": False,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
            "reducer": reducer,
            "refId": ref,
            "type": "reduce",
            "settings": {"mode": "replaceNN", "replaceWithValue": 0},
        },
    }


def threshold_query(ref: str, source: str, op: str, value: float) -> dict:
    return {
        "refId": ref,
        "queryType": "",
        "relativeTimeRange": {"from": 0, "to": 0},
        "datasourceUid": EXPR_DS,
        "model": {
            "conditions": [
                {
                    "evaluator": {"params": [value], "type": op},
                    "operator": {"type": "and"},
                    "query": {"params": [ref]},
                    "reducer": {"params": [], "type": "last"},
                    "type": "query",
                }
            ],
            "datasource": {"type": "__expr__", "uid": EXPR_DS},
            "expression": source,
            "hide": False,
            "intervalMs": 1000,
            "maxDataPoints": 43200,
            "refId": ref,
            "type": "threshold",
        },
    }


def alert_rule(
    uid: str,
    title: str,
    expr: str,
    threshold: float,
    *,
    op: str = "gt",
    for_duration: str = "2m",
    severity: str = "warning",
    dashboard_uid: str | None = None,
    panel_id: int | None = None,
    summary: str = "",
    description: str = "",
    no_data: str = "OK",
    reducer: str = "last",
) -> dict:
    annotations = {
        "summary": summary or title,
        "description": description or expr,
    }
    if dashboard_uid and panel_id is not None:
        annotations["__dashboardUid__"] = dashboard_uid
        annotations["__panelId__"] = str(panel_id)

    return {
        "uid": uid,
        "title": title,
        "condition": "C",
        "data": [
            prom_alert_query("A", expr),
            reduce_query("B", "A", reducer=reducer),
            threshold_query("C", "B", op, threshold),
        ],
        "for": for_duration,
        "noDataState": no_data,
        "execErrState": "Error",
        "annotations": annotations,
        "labels": {"severity": severity, "team": "roboshop"},
        "isPaused": False,
    }


def upsert_rule_group(group: str, rules: list[dict]) -> None:
    payload = {"title": group, "interval": RULE_INTERVAL, "rules": rules}
    api(
        "PUT",
        f"/api/v1/provisioning/folder/{FOLDER_UID}/rule-groups/{group}",
        payload,
        extra_headers={"X-Disable-Provenance": "true"},
    )
    print(f"  {group}: {len(rules)} rules")


def ensure_email_contact_point() -> None:
    if not ALERT_EMAIL:
        print("  (skip email: set GRAFANA_ALERT_EMAIL in config.env)")
        return
    api(
        "PUT",
        f"/api/v1/provisioning/contact-points/{CONTACT_UID}",
        {
            "uid": CONTACT_UID,
            "name": CONTACT_UID,
            "type": "email",
            "settings": {
                "addresses": ALERT_EMAIL,
                "singleEmail": False,
            },
            "disableResolveMessage": False,
        },
        extra_headers={"X-Disable-Provenance": "true"},
    )
    print(f"  contact point → {ALERT_EMAIL}")


def ensure_notification_policy() -> None:
    if not ALERT_EMAIL:
        return
    api(
        "PUT",
        "/api/v1/provisioning/policies",
        {
            "receiver": CONTACT_UID,
            "group_by": ["grafana_folder", "alertname"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "4h",
            "routes": [],
        },
        extra_headers={"X-Disable-Provenance": "true"},
    )
    print(f"  notification policy → {CONTACT_UID}")


def build_traefik_rules() -> list[dict]:
    err_rate = 'sum(rate(traefik_service_requests_total{code=~"4..|5.."}[5m]))'
    err_5xx = 'sum(rate(traefik_service_requests_total{code=~"5.."}[5m]))'
    p95 = (
        "histogram_quantile(0.95, "
        "sum by (le) (rate(traefik_service_request_duration_seconds_bucket[5m])))"
    )

    return [
        alert_rule(
            "roboshop-traefik-errors",
            "Traefik ingress errors (4xx/5xx)",
            err_rate,
            ERROR_RATE_MIN,
            for_duration="2m",
            severity="warning",
            dashboard_uid="traefik-overview",
            panel_id=13,
            summary="Traefik is returning HTTP 4xx/5xx responses",
            description=(
                "Ingress error rate is above zero for at least 2 minutes. "
                "Check Traefik - Ingress Overview dashboard, Error Rate / Error Count panels."
            ),
        ),
        alert_rule(
            "roboshop-traefik-5xx",
            "Traefik ingress server errors (5xx)",
            err_5xx,
            ERROR_RATE_MIN,
            for_duration="1m",
            severity="critical",
            dashboard_uid="traefik-overview",
            panel_id=13,
            summary="Traefik is returning HTTP 5xx responses",
            description="Server-side ingress errors detected. Investigate backend pods and Traefik routing.",
        ),
        alert_rule(
            "roboshop-traefik-latency",
            "Traefik ingress high P95 latency",
            p95,
            LATENCY_P95_SEC,
            for_duration="5m",
            severity="warning",
            dashboard_uid="traefik-overview",
            panel_id=11,
            summary="Traefik P95 latency is high",
            description=f"P95 request latency exceeded {LATENCY_P95_SEC}s for 5 minutes.",
        ),
    ]


def build_roboshop_rules() -> list[dict]:
    ns = ROBOSHOP_NS
    dep = f'deployment=~"roboshop-.*"'
    pod = 'pod=~"roboshop-.*"'
    ctr = 'container!="", container!="POD"'

    cpu_lim_pct = (
        f"max by (pod) ("
        f"100 * sum by (pod) (rate(container_cpu_usage_seconds_total{{namespace=\"{ns}\", {pod}, {ctr}}}[5m])) "
        f"/ sum by (pod) (kube_pod_container_resource_limits{{namespace=\"{ns}\", {pod}, resource=\"cpu\", container!=\"\"}})"
        f")"
    )
    mem_lim_pct = (
        f"max by (pod) ("
        f"100 * sum by (pod) (container_memory_working_set_bytes{{namespace=\"{ns}\", {pod}, {ctr}}}) "
        f"/ sum by (pod) (kube_pod_container_resource_limits{{namespace=\"{ns}\", {pod}, resource=\"memory\", container!=\"\"}})"
        f")"
    )
    replica_gap = (
        f"kube_deployment_spec_replicas{{namespace=\"{ns}\", {dep}}} "
        f"- kube_deployment_status_replicas_available{{namespace=\"{ns}\", {dep}}}"
    )
    restarts = f"sum by (pod) (increase(kube_pod_container_status_restarts_total{{namespace=\"{ns}\", {pod}}}[15m]))"
    roboshop_err = (
        'sum by (exported_service) (rate(traefik_service_requests_total'
        '{exported_service=~"default-roboshop-.*", code=~"4..|5.."}[5m]))'
    )

    return [
        alert_rule(
            "roboshop-service-traefik-errors",
            "Roboshop service HTTP errors via Traefik",
            roboshop_err,
            ERROR_RATE_MIN,
            for_duration="2m",
            severity="warning",
            dashboard_uid="roboshop-cluster",
            panel_id=26,
            summary="Roboshop backend returned HTTP 4xx/5xx via Traefik",
            description="One or more roboshop services are returning client or server errors through the ingress.",
            reducer="max",
        ),
        alert_rule(
            "roboshop-replica-mismatch",
            "Roboshop deployment replica mismatch",
            replica_gap,
            0,
            op="gt",
            for_duration="5m",
            severity="critical",
            dashboard_uid="roboshop-cluster",
            panel_id=23,
            summary="Available replicas are below desired count",
            description="A roboshop deployment has fewer available pods than desired for at least 5 minutes.",
            reducer="max",
        ),
        alert_rule(
            "roboshop-pod-restart",
            "Roboshop pod container restart",
            restarts,
            0,
            op="gt",
            for_duration="1m",
            severity="warning",
            dashboard_uid="roboshop-cluster",
            panel_id=20,
            summary="Roboshop pod restarted in the last 15 minutes",
            description="Container restart detected. Check pod logs and resource pressure.",
            reducer="max",
        ),
        alert_rule(
            "roboshop-cpu-limit-high",
            "Roboshop pod CPU near limit",
            cpu_lim_pct,
            CPU_LIMIT_PCT,
            for_duration="5m",
            severity="warning",
            dashboard_uid="roboshop-cluster",
            panel_id=4,
            summary=f"Roboshop pod CPU usage exceeded {CPU_LIMIT_PCT:.0f}% of limit",
            description="CPU working set is approaching the Kubernetes limit. Consider scaling or raising limits.",
            reducer="max",
        ),
        alert_rule(
            "roboshop-memory-limit-high",
            "Roboshop pod memory near limit",
            mem_lim_pct,
            MEM_LIMIT_PCT,
            for_duration="5m",
            severity="warning",
            dashboard_uid="roboshop-cluster",
            panel_id=6,
            summary=f"Roboshop pod memory exceeded {MEM_LIMIT_PCT:.0f}% of limit",
            description="Memory working set is approaching the Kubernetes limit. Risk of OOMKill.",
            reducer="max",
        ),
    ]


def main() -> int:
    if not GRAFANA_PASSWORD:
        print("GRAFANA_PASSWORD is required", file=sys.stderr)
        return 1

    print(f"Grafana: {GRAFANA_URL}")
    ensure_folder()
    print("Deploying email notifications:")
    ensure_email_contact_point()
    ensure_notification_policy()
    print("Deploying alert rule groups:")
    upsert_rule_group("traefik-alerts", build_traefik_rules())
    upsert_rule_group("roboshop-alerts", build_roboshop_rules())
    print("Done.")
    if ALERT_EMAIL:
        print(f"Alert emails will be sent to: {ALERT_EMAIL}")
    else:
        print("Set GRAFANA_ALERT_EMAIL (and SMTP vars) in config.env to enable email notifications.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
