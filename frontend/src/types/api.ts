export const HTTP_METHODS = [
  "GET",
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
  "HEAD",
  "OPTIONS",
] as const;

export type HttpMethod = (typeof HTTP_METHODS)[number];
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface ApiRequest {
  url: string;
  method: HttpMethod;
  headers: Record<string, string>;
  query_params: Record<string, string>;
  body?: JsonValue;
  timeout: number;
}

export interface ApiError {
  code: string;
  message: string;
}

export interface ApiResponse {
  status_code: number | null;
  headers: Record<string, string>;
  body: JsonValue | null;
  content_type: string | null;
  is_json: boolean;
  execution_time_ms: number | null;
  error: ApiError | null;
}

export interface KeyValuePair {
  id: string;
  key: string;
  value: string;
  enabled: boolean;
}

export type BoundaryLocation = "path" | "query" | "header" | "body";
export type BoundaryDataType = "string" | "number" | "array" | "object";

export interface BoundaryInput {
  name: string;
  location: BoundaryLocation;
  data_type: BoundaryDataType;
  minimum?: number;
  maximum?: number;
}

export interface BoundaryTestCase {
  id: string;
  label: string;
  input_name: string;
  location: BoundaryLocation;
  data_type: BoundaryDataType;
  operation: "set" | "remove";
  value: JsonValue;
  path_values?: Record<string, JsonValue>;
}

export interface OpenApiParameter {
  name: string;
  location: string;
  required: boolean;
  schema: JsonValue;
}

export interface OpenApiOperation {
  id: string;
  method: HttpMethod;
  path: string;
  summary: string;
  tags: string[];
  request: ApiRequest;
  parameters: OpenApiParameter[];
  request_body_schema: JsonValue | null;
  cases: BoundaryTestCase[];
  warnings: string[];
}

export interface OpenApiSchema {
  name: string;
  schema: JsonValue;
}

export interface OpenApiImportResponse {
  title: string;
  version: string;
  specification_version: string;
  operations: OpenApiOperation[];
  servers: string[];
  schemas: OpenApiSchema[];
  warnings: string[];
}

export interface TestCollection {
  id: string;
  name: string;
  created_at: string;
  updated_at?: string;
  saved_test_count?: number;
}

export interface SavedTest {
  id: string;
  collection_id: string;
  name: string;
  request: ApiRequest;
  test_cases: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface ExecutionRecord {
  id: string;
  collection_id: string | null;
  saved_test_id: string | null;
  replay_of: string | null;
  method: HttpMethod;
  endpoint: string;
  status_code: number | null;
  outcome: "passed" | "failed" | "errored";
  error_category: string | null;
  duration_ms: number | null;
  request: ApiRequest;
  response: ApiResponse;
  test_case: Record<string, unknown> | null;
  created_at: string;
}

export interface PlatformAnalytics {
  total_requests: number;
  total_test_cases: number;
  saved_test_case_definitions: number;
  passed_tests: number;
  failed_tests: number;
  errored_tests: number;
  recent_failures: ExecutionRecord[];
  failing_endpoints: Array<{
    method: string;
    endpoint: string;
    failures: number;
  }>;
  trends: Array<{
    date: string;
    total: number;
    passed: number;
    failed: number;
  }>;
}

export interface AiAnalysis {
  error_summary: string;
  possible_root_cause: string;
  explanation: string;
  debugging_steps: string[];
  potential_issues: string[];
  suggested_corrections: string[];
  provider: string;
  model: string;
}
