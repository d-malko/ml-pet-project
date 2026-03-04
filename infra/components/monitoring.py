"""
Monitoring component.
Deploys:
  - Prometheus ServiceMonitor (auto-discovery of ml-model metrics)
  - PrometheusRule (alerting rules)
  - AnalysisTemplate (SLO checks during canary rollout)
"""
import pulumi
import pulumi_kubernetes as k8s

from config import MonitoringConfig


class MonitoringComponent(pulumi.ComponentResource):

    def __init__(
        self,
        name: str,
        cfg: MonitoringConfig,
        provider: k8s.Provider,
        opts: pulumi.ResourceOptions = None,
    ):
        super().__init__("ml-pet-project:infra:MonitoringComponent", name, {}, opts)

        base_opts = pulumi.ResourceOptions(parent=self, provider=provider)

        # ── ServiceMonitor ────────────────────────────────────────
        self.service_monitor = k8s.apiextensions.CustomResource(
            "service-monitor",
            api_version="monitoring.coreos.com/v1",
            kind="ServiceMonitor",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model",
                namespace=cfg.namespace,
                labels={"release": "kube-prometheus-stack"},
            ),
            spec={
                "selector":          {"matchLabels": {"app": "ml-model"}},
                "namespaceSelector": {"matchNames": ["ml-serving"]},
                "endpoints": [{
                    "port":        "http",
                    "path":        "/metrics",
                    "interval":    "15s",
                    "honorLabels": True,
                }],
            },
            opts=base_opts,
        )

        # ── PrometheusRule (alerting) ─────────────────────────────
        self.prometheus_rule = k8s.apiextensions.CustomResource(
            "prometheus-rule",
            api_version="monitoring.coreos.com/v1",
            kind="PrometheusRule",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-alerts",
                namespace=cfg.namespace,
                labels={"release": "kube-prometheus-stack"},
            ),
            spec={
                "groups": [{
                    "name":     "ml-model.rules",
                    "interval": "30s",
                    "rules": [
                        {
                            "alert": "MLModelHighErrorRate",
                            "expr": (
                                'sum(rate(http_requests_total{status=~"5.."}[5m]))'
                                " / sum(rate(http_requests_total[5m])) > 0.01"
                            ),
                            "for": "2m",
                            "labels":      {"severity": "critical", "team": "mlops"},
                            "annotations": {
                                "summary":     "ML Model error rate > 1%",
                                "description": "Error rate: {{ $value | humanizePercentage }}",
                            },
                        },
                        {
                            "alert": "MLModelHighLatency",
                            "expr": (
                                "histogram_quantile(0.99,"
                                " sum(rate(http_request_duration_seconds_bucket[5m])) by (le)"
                                ") > 0.5"
                            ),
                            "for": "3m",
                            "labels":      {"severity": "warning"},
                            "annotations": {"summary": "P99 latency > 500ms"},
                        },
                        {
                            "alert": "MLModelPodDown",
                            "expr": 'up{job="ml-model"} == 0',
                            "for": "1m",
                            "labels":      {"severity": "critical"},
                            "annotations": {"summary": "ML Model pod is down"},
                        },
                    ],
                }]
            },
            opts=base_opts,
        )

        # ── AnalysisTemplate (SLO checks during canary) ───────────
        self.analysis_template = k8s.apiextensions.CustomResource(
            "analysis-template",
            api_version="argoproj.io/v1alpha1",
            kind="AnalysisTemplate",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="ml-model-slo-check",
                namespace="ml-serving",
            ),
            spec={
                "args": [{"name": "service-name"}],
                "metrics": [
                    {
                        "name":         "error-rate",
                        "interval":     "1m",
                        "count":        3,
                        "failureLimit": 1,
                        "provider": {
                            "prometheus": {
                                "address": cfg.prometheus_address,
                                "query": (
                                    'sum(rate(http_requests_total'
                                    '{kubernetes_service_name="{{args.service-name}}"'
                                    ',status=~"5.."}[2m]))'
                                    " / sum(rate(http_requests_total"
                                    '{kubernetes_service_name="{{args.service-name}}"}[2m]))'
                                ),
                            }
                        },
                        "successCondition": "result[0] < 0.01",
                        "failureCondition": "result[0] > 0.05",
                    },
                    {
                        "name":         "p99-latency",
                        "interval":     "1m",
                        "count":        3,
                        "failureLimit": 1,
                        "provider": {
                            "prometheus": {
                                "address": cfg.prometheus_address,
                                "query": (
                                    "histogram_quantile(0.99, sum("
                                    "rate(http_request_duration_seconds_bucket"
                                    '{kubernetes_service_name="{{args.service-name}}"}[2m])'
                                    ") by (le))"
                                ),
                            }
                        },
                        "successCondition": "result[0] < 0.5",
                        "failureCondition": "result[0] > 1.0",
                    },
                    {
                        "name":         "service-health",
                        "interval":     "30s",
                        "count":        5,
                        "failureLimit": 2,
                        "provider": {
                            "web": {
                                "url": (
                                    "http://{{args.service-name}}"
                                    ".ml-serving.svc.cluster.local/health"
                                ),
                                "timeoutSeconds": 10,
                            }
                        },
                        "successCondition": 'result == "healthy"',
                    },
                ],
            },
            opts=base_opts,
        )

        self.register_outputs({})
