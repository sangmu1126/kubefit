export type RiskLevel = "low" | "medium" | "high" | "unknown";
export type CheckStatus = "pass" | "warning" | "block";
export type DecimalValue = number | string;

export interface EvaluationRequest {
  current: {
    cpu_request_millicores: number;
    cpu_limit_millicores: number;
    memory_request_mib: number;
    memory_limit_mib: number;
  };
  observed: {
    cpu_p95_millicores: number;
    memory_p99_mib: number;
    cpu_max_millicores?: number;
    memory_max_mib?: number;
    observation_days: number;
    sample_count?: number;
    observation_coverage?: number;
    desired_replicas?: number;
    available_replicas?: number;
    observed_replicas?: number;
    metric_pod_count?: number;
    cpu_throttling_p95_percent?: number;
    cpu_throttling_max_percent?: number;
    cpu_throttling_sample_count?: number;
    cpu_throttling_pod_count?: number;
    cpu_throttling_observation_coverage?: number;
    container_status_count?: number;
    restart_count?: number;
    oom_killed_count?: number;
  };
  cost_assumptions: {
    currency: "USD";
    cpu_core_hour_usd: number;
    memory_gib_hour_usd: number;
    monthly_hours: number;
    price_source: string;
  };
  replica_count: number;
}

export interface Resources {
  cpu_request_millicores: number;
  cpu_limit_millicores: number;
  memory_request_mib: number;
  memory_limit_mib: number;
}

export interface EvaluationResult {
  current: Resources;
  recommendation: {
    recommended: Resources;
    cpu_request_change_percent: number;
    memory_request_change_percent: number;
    readiness: {
      status: "ready" | "insufficient_data";
      reasons: string[];
    };
    risk: {
      oom: RiskLevel;
      cpu_throttling: RiskLevel;
      reasons: string[];
    };
    evidence: string[];
  };
  cost: {
    assumptions: {
      currency: "USD";
      cpu_core_hour_usd: DecimalValue;
      memory_gib_hour_usd: DecimalValue;
      monthly_hours: DecimalValue;
      price_source: string;
    };
    replica_count: number;
    basis: "resource_requests";
    current: { cpu_usd: DecimalValue; memory_usd: DecimalValue; total_usd: DecimalValue };
    recommended: { cpu_usd: DecimalValue; memory_usd: DecimalValue; total_usd: DecimalValue };
    monthly_delta_usd: DecimalValue;
    savings_percent: DecimalValue;
    caveats: string[];
  };
  patch_eligibility: {
    status: "eligible" | "blocked";
    checks: Array<{ code: string; status: CheckStatus; reason: string }>;
    blocking_reasons: string[];
    warnings: string[];
  };
}
