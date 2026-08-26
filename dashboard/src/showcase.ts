import type { EvaluationRequest } from "./types";

export const DECISION_JOURNEY_ID = "decision-journey";
export const VERIFIED_PAIR_ID = "benchmark-pair-dbc41864dd0dba9537ef228ebb340f60";

const repositoryRoot = "https://github.com/sangmu1126/kubefit";

export const decisionJourneyEvidence = {
  sources: {
    refinement: `${repositoryRoot}/blob/main/docs/devlog/0060-validation-informed-cpu-floor.md`,
    publication: `${repositoryRoot}/blob/main/docs/devlog/0061-live-pair-draft-publication.md`,
    replay: `${repositoryRoot}/blob/main/docs/devlog/0064-public-replayable-pair-demo.md`,
    draftPullRequest: `${repositoryRoot}/pull/23`,
  },
  observation: {
    requests: 100_501,
    errors: 0,
    usageCoveragePercent: 100,
    throttlingCoveragePercent: 100,
  },
  rejected: {
    cpuRequestMillicores: 10,
    cpuLimitMillicores: 20,
    steadyP99ChangePercent: 40.804,
  },
  refined: {
    cpuRequestMillicores: 20,
    cpuLimitMillicores: 40,
    memoryRequestMiB: 32,
    memoryLimitMiB: 48,
  },
  costProjection: {
    currentUsd: "73.000000",
    recommendedUsd: "1.396125",
    changePercent: "-98.088",
  },
} as const;

export const recordedInitialEvaluationRequest: EvaluationRequest = {
  current: {
    cpu_request_millicores: 1000,
    cpu_limit_millicores: 2000,
    memory_request_mib: 2048,
    memory_limit_mib: 4096,
  },
  observed: {
    cpu_p95_millicores: 3.6588310766738568,
    cpu_max_millicores: 4.079267681109583,
    memory_p99_mib: 7.68671875,
    memory_max_mib: 7.83203125,
    observation_days: 0.041666666666666664,
    sample_count: 122,
    observation_coverage: 1,
    desired_replicas: 2,
    available_replicas: 2,
    observed_replicas: 2,
    metric_pod_count: 2,
    cpu_throttling_p95_percent: 0,
    cpu_throttling_max_percent: 0,
    cpu_throttling_sample_count: 122,
    cpu_throttling_pod_count: 2,
    cpu_throttling_observation_coverage: 1,
    container_status_count: 2,
    restart_count: 0,
    oom_killed_count: 0,
  },
  cost_assumptions: {
    currency: "USD",
    cpu_core_hour_usd: 0.04,
    memory_gib_hour_usd: 0.005,
    monthly_hours: 730,
    price_source: "example://local-model",
  },
  replica_count: 2,
};

export const recordedYamlDiff = `--- a/deploy/demo/overprovisioned-api.yaml
+++ b/deploy/demo/overprovisioned-api.yaml
@@ -20,8 +20,8 @@
           resources:
             requests:
-              cpu: "1000m"
-              memory: "2Gi"
+              cpu: "20m"
+              memory: "32Mi"
             limits:
-              cpu: "2000m"
-              memory: "4Gi"
+              cpu: "40m"
+              memory: "48Mi"`;
