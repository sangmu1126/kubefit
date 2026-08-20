import type { EvaluationRequest } from "./types";

export const eligibleScenario: EvaluationRequest = {
  current: {
    cpu_request_millicores: 1000,
    cpu_limit_millicores: 2000,
    memory_request_mib: 2048,
    memory_limit_mib: 4096,
  },
  observed: {
    cpu_p95_millicores: 230,
    memory_p99_mib: 710,
    cpu_max_millicores: 420,
    memory_max_mib: 980,
    observation_days: 7,
    sample_count: 2016,
    observation_coverage: 0.95,
    desired_replicas: 2,
    available_replicas: 2,
    observed_replicas: 2,
    metric_pod_count: 2,
    cpu_throttling_p95_percent: 0.4,
    cpu_throttling_max_percent: 0.9,
    cpu_throttling_sample_count: 2016,
    cpu_throttling_pod_count: 2,
    cpu_throttling_observation_coverage: 0.95,
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

export const insufficientScenario: EvaluationRequest = {
  ...structuredClone(eligibleScenario),
  observed: {
    cpu_p95_millicores: 230,
    memory_p99_mib: 710,
    observation_days: 1,
  },
};
