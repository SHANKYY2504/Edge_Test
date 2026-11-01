import type {
  ApiRequest,
  ApiResponse,
  BoundaryInput,
  BoundaryTestCase,
  AiAnalysis,
  ExecutionRecord,
  OpenApiImportResponse,
  PlatformAnalytics,
  SavedTest,
  TestCollection,
} from "../types/api";

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
  };
}

export class ApiClientError extends Error {
  readonly code: string;
  readonly status: number | null;

  constructor(message: string, code: string, status: number | null = null) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.status = status;
  }
}

async function platformFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1${path}`, init);
  } catch {
    throw new ApiClientError(
      "Could not connect to the EdgeTest backend. Check that it is running.",
      "network_error",
    );
  }
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = payload as ErrorEnvelope | null;
    throw new ApiClientError(
      envelope?.error?.message ??
        (typeof payload === "object" &&
        payload !== null &&
        "detail" in payload &&
        typeof payload.detail === "string"
          ? payload.detail
          : `Backend returned HTTP ${response.status}.`),
      envelope?.error?.code ?? "backend_error",
      response.status,
    );
  }
  return payload as T;
}

function jsonBody(value: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}

export async function fetchAnalytics(): Promise<PlatformAnalytics> {
  const result = await platformFetch<unknown>("/analytics");
  if (
    typeof result !== "object" ||
    result === null ||
    !("total_requests" in result) ||
    typeof result.total_requests !== "number" ||
    !("total_test_cases" in result) ||
    typeof result.total_test_cases !== "number" ||
    !("saved_test_case_definitions" in result) ||
    typeof result.saved_test_case_definitions !== "number" ||
    !("passed_tests" in result) ||
    typeof result.passed_tests !== "number" ||
    !("failed_tests" in result) ||
    typeof result.failed_tests !== "number" ||
    !("errored_tests" in result) ||
    typeof result.errored_tests !== "number" ||
    !("recent_failures" in result) ||
    !Array.isArray(result.recent_failures) ||
    !("failing_endpoints" in result) ||
    !Array.isArray(result.failing_endpoints) ||
    !("trends" in result) ||
    !Array.isArray(result.trends)
  ) {
    throw new ApiClientError("The backend returned invalid analytics data.", "invalid_backend_response");
  }
  return result as PlatformAnalytics;
}

export async function fetchCollections(): Promise<TestCollection[]> {
  const result = await platformFetch<unknown>("/collections");
  if (!Array.isArray(result)) {
    throw new ApiClientError("The backend returned invalid collection data.", "invalid_backend_response");
  }
  return result as TestCollection[];
}

export async function createCollection(name: string): Promise<TestCollection> {
  return platformFetch<TestCollection>("/collections", jsonBody({ name }));
}

export async function renameCollection(
  id: string,
  name: string,
): Promise<TestCollection> {
  return platformFetch<TestCollection>(
    `/collections/${encodeURIComponent(id)}`,
    { ...jsonBody({ name }), method: "PATCH" },
  );
}

export async function deleteCollection(id: string): Promise<void> {
  return platformFetch<void>(`/collections/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function fetchSavedTests(id: string): Promise<SavedTest[]> {
  const result = await platformFetch<unknown>(
    `/collections/${encodeURIComponent(id)}/tests`,
  );
  if (!Array.isArray(result)) {
    throw new ApiClientError("The backend returned invalid saved-test data.", "invalid_backend_response");
  }
  return result as SavedTest[];
}

export async function saveTest(
  collectionId: string,
  payload: { name: string; request: ApiRequest; test_cases: Array<Record<string, unknown>> },
): Promise<SavedTest> {
  return platformFetch<SavedTest>(
    `/collections/${encodeURIComponent(collectionId)}/tests`,
    jsonBody(payload),
  );
}

export async function deleteSavedTest(id: string): Promise<void> {
  return platformFetch<void>(`/tests/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function executeSavedTest(
  id: string,
  confirmed: boolean,
): Promise<ExecutionRecord> {
  const result = await platformFetch<{ execution: ExecutionRecord }>(
    `/tests/${encodeURIComponent(id)}/execute`,
    jsonBody({ confirmed }),
  );
  return result.execution;
}

export async function fetchFailures(filters: {
  endpoint?: string;
  method?: string;
  status_code?: string;
  error_type?: string;
} = {}): Promise<ExecutionRecord[]> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) query.set(key, value);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  const result = await platformFetch<unknown>(`/failures${suffix}`);
  if (!Array.isArray(result)) {
    throw new ApiClientError("The backend returned invalid failure data.", "invalid_backend_response");
  }
  return result as ExecutionRecord[];
}

export async function replayExecution(
  id: string,
  confirmed: boolean,
): Promise<{ original: ExecutionRecord; replay: ExecutionRecord }> {
  return platformFetch(`/executions/${encodeURIComponent(id)}/replay`, {
    ...jsonBody({ confirmed }),
  });
}

export async function analyzeExecution(id: string): Promise<AiAnalysis> {
  return platformFetch<AiAnalysis>(
    `/executions/${encodeURIComponent(id)}/analyze`,
    jsonBody({ consent: true }),
  );
}

export function exportExecutions(format: "json" | "csv"): string {
  return `${API_BASE_URL}/api/v1/executions/export?format=${format}`;
}

export async function executeRequest(
  request: ApiRequest,
  metadata: { testCase?: string } = {},
): Promise<ApiResponse> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(
    () => controller.abort(),
    (request.timeout + 5) * 1000,
  );

  try {
    const query = new URLSearchParams();
    if (metadata.testCase) query.set("test_case", metadata.testCase);
    const suffix = query.size ? `?${query.toString()}` : "";
    const response = await fetch(`${API_BASE_URL}/api/v1/requests/execute${suffix}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });

    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const envelope = payload as ErrorEnvelope | null;
      throw new ApiClientError(
        envelope?.error?.message ?? `Backend returned HTTP ${response.status}.`,
        envelope?.error?.code ?? "backend_error",
        response.status,
      );
    }

    if (!isApiResponse(payload)) {
      throw new ApiClientError(
        "The backend returned an unexpected response.",
        "invalid_backend_response",
      );
    }
    return payload;
  } catch (error) {
    if (error instanceof ApiClientError) {
      throw error;
    }
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiClientError(
        "The connection to the EdgeTest backend timed out.",
        "backend_timeout",
      );
    }
    throw new ApiClientError(
      "Could not connect to the EdgeTest backend. Check that it is running.",
      "network_error",
    );
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function generateBoundaryCases(
  inputs: BoundaryInput[],
): Promise<BoundaryTestCase[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/boundary/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ inputs }),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = payload as ErrorEnvelope | null;
    throw new ApiClientError(
      envelope?.error?.message ?? `Backend returned HTTP ${response.status}.`,
      envelope?.error?.code ?? "backend_error",
      response.status,
    );
  }
  if (!isBoundaryGenerationResponse(payload)) {
    throw new ApiClientError(
      "The backend returned an unexpected boundary-test response.",
      "invalid_backend_response",
    );
  }
  return payload.cases;
}

export async function importOpenApiSpecification(
  source:
    | { specification: string; base_url?: string }
    | { url: string; base_url?: string },
): Promise<OpenApiImportResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/openapi/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(source),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const envelope = payload as ErrorEnvelope | null;
    throw new ApiClientError(
      envelope?.error?.message ??
        (typeof payload === "object" &&
        payload !== null &&
        "detail" in payload &&
        typeof payload.detail === "string"
          ? payload.detail
          : `Backend returned HTTP ${response.status}.`),
      envelope?.error?.code ?? "openapi_import_error",
      response.status,
    );
  }
  if (!isOpenApiImportResponse(payload)) {
    throw new ApiClientError(
      "The backend returned an unexpected OpenAPI import response.",
      "invalid_backend_response",
    );
  }
  return payload;
}

function isOpenApiImportResponse(value: unknown): value is OpenApiImportResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "title" in value &&
    typeof value.title === "string" &&
    "version" in value &&
    typeof value.version === "string" &&
    "specification_version" in value &&
    typeof value.specification_version === "string" &&
    "operations" in value &&
    Array.isArray(value.operations) &&
    "servers" in value &&
    Array.isArray(value.servers) &&
    value.servers.every((server: unknown) => typeof server === "string") &&
    "schemas" in value &&
    Array.isArray(value.schemas) &&
    value.operations.every(
      (operation: unknown) =>
        typeof operation === "object" &&
        operation !== null &&
        "id" in operation &&
        typeof operation.id === "string" &&
        "method" in operation &&
        typeof operation.method === "string" &&
        "path" in operation &&
        typeof operation.path === "string" &&
        "request" in operation &&
        typeof operation.request === "object" &&
        operation.request !== null &&
        "cases" in operation &&
        Array.isArray(operation.cases),
    )
  );
}

function isBoundaryGenerationResponse(
  value: unknown,
): value is { cases: BoundaryTestCase[] } {
  if (
    typeof value !== "object" ||
    value === null ||
    !("cases" in value) ||
    !Array.isArray(value.cases)
  ) {
    return false;
  }
  return value.cases.every(
    (testCase: unknown) =>
      typeof testCase === "object" &&
      testCase !== null &&
      "id" in testCase &&
      typeof testCase.id === "string" &&
      "label" in testCase &&
      typeof testCase.label === "string" &&
      "input_name" in testCase &&
      typeof testCase.input_name === "string" &&
      "location" in testCase &&
      ["path", "query", "header", "body"].includes(String(testCase.location)) &&
      "data_type" in testCase &&
      ["string", "number", "array", "object"].includes(String(testCase.data_type)) &&
      "operation" in testCase &&
      ["set", "remove"].includes(String(testCase.operation)) &&
      "value" in testCase,
  );
}

function isApiResponse(value: unknown): value is ApiResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const response = value as Partial<ApiResponse>;
  return (
    "status_code" in response &&
    typeof response.headers === "object" &&
    "body" in response &&
    "error" in response
  );
}
