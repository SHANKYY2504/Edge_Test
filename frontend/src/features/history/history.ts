import type { ApiRequest, ApiResponse, KeyValuePair } from "../../types/api";

const SENSITIVE_HEADER =
  /auth|cookie|token|secret|api[-_]?key|password/i;
const SENSITIVE_QUERY =
  /^(access_token|refresh_token|token|api[-_]?key|key|password|secret|authorization)$/i;
const SENSITIVE_BODY_KEY = /auth|cookie|token|secret|api[-_]?key|password/i;

function redactBodyValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redactBodyValue);
  }
  if (typeof value !== "object" || value === null) {
    return value;
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, child]) => [
      key,
      SENSITIVE_BODY_KEY.test(key) ? "[REDACTED]" : redactBodyValue(child),
    ]),
  );
}

function redactBody(body: string): string {
  if (!body.trim()) return body;
  try {
    return JSON.stringify(redactBodyValue(JSON.parse(body)), null, 2);
  } catch {
    return body;
  }
}

export interface RequestConfiguration {
  method: ApiRequest["method"];
  url: string;
  headers: KeyValuePair[];
  queryParams: KeyValuePair[];
  body: string;
  timeout: number;
}

export interface HistoryEntry {
  id: string;
  method: ApiRequest["method"];
  url: string;
  timestamp: string;
  status: number | null;
  duration: number | null;
  configuration: RequestConfiguration;
}

export function redactUrl(input: string): string {
  try {
    const url = new URL(input);
    url.username = "";
    url.password = "";
    for (const key of url.searchParams.keys()) {
      if (SENSITIVE_QUERY.test(key)) {
        url.searchParams.set(key, "[REDACTED]");
      }
    }
    return url.toString();
  } catch {
    return input
      .replace(/\/\/[^/@\s]+:[^/@\s]+@/, "//[REDACTED]@")
      .replace(
        /([?&](?:access_token|refresh_token|token|api[-_]?key|key|password|secret|authorization)=)[^&#]*/gi,
        "$1[REDACTED]",
      );
  }
}

export function redactConfiguration(
  configuration: RequestConfiguration,
): RequestConfiguration {
  return {
    ...configuration,
    url: redactUrl(configuration.url),
    body: redactBody(configuration.body),
    headers: configuration.headers.map((header) => ({
      ...header,
      value: SENSITIVE_HEADER.test(header.key) ? "[REDACTED]" : header.value,
    })),
  };
}

export function makeHistoryEntry(
  configuration: RequestConfiguration,
  response: ApiResponse,
): HistoryEntry {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    method: configuration.method,
    url: redactUrl(configuration.url),
    timestamp: new Date().toISOString(),
    status: response.status_code,
    duration: response.execution_time_ms,
    configuration: redactConfiguration(configuration),
  };
}
