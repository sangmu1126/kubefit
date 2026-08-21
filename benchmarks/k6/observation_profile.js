import http from "k6/http";
import { check } from "k6";

const profileVersion = "kubefit-observation-demo-v1";
const targetUrl = requiredEnv("KUBEFIT_TARGET_URL");

export const options = {
  discardResponseBodies: true,
  systemTags: ["status", "method", "scenario"],
  scenarios: {
    warmup: phase("warmup", 5, "10m", "0s"),
    steady: phase("steady", 25, "35m", "10m"),
    spike: phase("spike", 100, "5m", "45m"),
    recovery: phase("recovery", 25, "10m", "50m"),
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(99)<1000"],
    dropped_iterations: ["count==0"],
  },
};

function phase(name, rate, duration, startTime) {
  return {
    executor: "constant-arrival-rate",
    exec: "request",
    rate,
    timeUnit: "1s",
    duration,
    startTime,
    preAllocatedVUs: Math.max(rate, 10),
    maxVUs: Math.max(rate * 2, 20),
    tags: { kubefit_observation_phase: name },
  };
}

export function request() {
  const response = http.get(targetUrl);
  check(response, { "status is below 500": (result) => result.status < 500 });
}

export function handleSummary(data) {
  return {
    stdout: `${JSON.stringify({
      schema_version: 1,
      profile_version: profileVersion,
      duration_minutes: 60,
      requests: data.metrics.http_reqs?.values.count ?? 0,
      dropped_iterations: data.metrics.dropped_iterations?.values.count ?? 0,
      error_rate: data.metrics.http_req_failed?.values.rate ?? 0,
    }, null, 2)}\n`,
  };
}

function requiredEnv(name) {
  const value = __ENV[name];
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}
