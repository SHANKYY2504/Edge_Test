import { useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileUp,
  LoaderCircle,
  Play,
  ShieldAlert,
} from "lucide-react";

import {
  ApiClientError,
  executeRequest,
  importOpenApiSpecification,
} from "../../services/apiClient";
import type {
  ApiRequest,
  ApiResponse,
  BoundaryTestCase,
  OpenApiImportResponse,
  OpenApiOperation,
} from "../../types/api";
import type { RequestConfiguration } from "../history/history";
import { makeBoundaryRequest } from "../boundary/boundaryCases";

const MAX_SPECIFICATION_BYTES = 1_048_576;
const MAX_SUITE_CASES = 100;

interface OpenApiRunResult {
  operationKey: string;
  testCase: BoundaryTestCase;
  request: ApiRequest | null;
  response: ApiResponse | null;
  error: string | null;
  outcome: "passed" | "failed" | "errored";
}

function messageFrom(error: unknown): string {
  return error instanceof ApiClientError
    ? error.message
    : error instanceof Error
      ? error.message
      : "OpenAPI import failed.";
}

function createRequestConfiguration(request: ApiRequest): RequestConfiguration {
  return {
    method: request.method,
    url: request.url,
    headers: Object.entries(request.headers).map(([key, value], index) => ({
      id: `openapi-header-${index}`,
      key,
      value,
      enabled: true,
    })),
    queryParams: Object.entries(request.query_params).map(([key, value], index) => ({
      id: `openapi-query-${index}`,
      key,
      value,
      enabled: true,
    })),
    body:
      request.body === undefined || request.body === null
        ? ""
        : JSON.stringify(request.body, null, 2),
    timeout: request.timeout,
  };
}

function responseOutcome(response: ApiResponse): OpenApiRunResult["outcome"] {
  if (response.error || response.status_code === null) return "errored";
  return response.status_code < 400 ? "passed" : "failed";
}

function getErrorMessage(error: unknown): string {
  return error instanceof ApiClientError
    ? `${error.code}: ${error.message}`
    : error instanceof Error
      ? error.message
      : "The request could not be completed.";
}

function displayResult(result: OpenApiRunResult): string {
  if (result.error) return result.error;
  return JSON.stringify(result.response, null, 2) ?? "";
}

export function OpenApiTester({
  onGeneratedCases,
}: {
  onGeneratedCases?: (cases: Array<Record<string, unknown>>) => void;
}) {
  const [specUrl, setSpecUrl] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [selectedServer, setSelectedServer] = useState("");
  const [specification, setSpecification] = useState<OpenApiImportResponse | null>(
    null,
  );
  const [selectedOperations, setSelectedOperations] = useState<Set<string>>(
    new Set(),
  );
  const [expandedOperations, setExpandedOperations] = useState<Set<string>>(
    new Set(),
  );
  const [selectedCases, setSelectedCases] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<OpenApiRunResult[]>([]);
  const [message, setMessage] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const [isRunning, setIsRunning] = useState(false);

  const installSpecification = (loaded: OpenApiImportResponse) => {
    setSpecification(loaded);
    setSelectedServer(loaded.servers[0] ?? "");
    onGeneratedCases?.(
      loaded.operations.flatMap((operation) =>
        operation.cases.map((testCase) => ({
          ...testCase,
          api_operation_id: operation.id,
          method: operation.method,
          path: operation.path,
        })),
      ),
    );
    setSelectedOperations(new Set());
    setExpandedOperations(new Set());
    setSelectedCases(new Set());
    setResults([]);
    setMessage(
      `${loaded.operations.length} operations discovered in ${loaded.title}.`,
    );
  };

  const importUrl = async () => {
    const trimmed = specUrl.trim();
    let parsed: URL;
    try {
      parsed = new URL(trimmed);
    } catch {
      setMessage("Enter a valid HTTP or HTTPS specification URL.");
      return;
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      setMessage("Specification URLs must use HTTP or HTTPS.");
      return;
    }
    if (parsed.username || parsed.password) {
      setMessage("Specification URLs must not contain credentials.");
      return;
    }
    setIsImporting(true);
    setMessage("");
    try {
      const base = baseUrl.trim();
      installSpecification(await importOpenApiSpecification({
        url: parsed.toString(),
        ...(base ? { base_url: base } : {}),
      }));
    } catch (error) {
      setSpecification(null);
      setMessage(messageFrom(error));
    } finally {
      setIsImporting(false);
    }
  };

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_SPECIFICATION_BYTES) {
      setMessage("Specification files must be 1 MiB or smaller.");
      return;
    }
    if (!/\.(json|ya?ml)$/i.test(file.name)) {
      setMessage("Choose a JSON or YAML specification file.");
      return;
    }
    setIsImporting(true);
    setMessage("");
    try {
      const base = baseUrl.trim();
      installSpecification(
        await importOpenApiSpecification({
          specification: await file.text(),
          ...(base ? { base_url: base } : {}),
        }),
      );
    } catch (error) {
      setSpecification(null);
      setMessage(messageFrom(error));
    } finally {
      setIsImporting(false);
    }
  };

  const operationKey = (index: number, operation: OpenApiOperation) =>
    `${index}-${operation.id}`;
  const caseKey = (key: string, testCase: BoundaryTestCase) =>
    `${key}::${testCase.id}`;

  const toggleOperation = (key: string, operation: OpenApiOperation) => {
    const shouldSelect = !selectedOperations.has(key);
    setSelectedOperations((previous) => {
      const next = new Set(previous);
      if (shouldSelect) next.add(key);
      else next.delete(key);
      return next;
    });
    setSelectedCases((previous) => {
      const next = new Set(previous);
      operation.cases.forEach((testCase) => {
        const id = caseKey(key, testCase);
        if (shouldSelect) next.add(id);
        else next.delete(id);
      });
      return next;
    });
    if (shouldSelect) {
      setExpandedOperations((previous) => new Set([...previous, key]));
    }
  };

  const executeCases = async (
    requested: Array<{
      operationKey: string;
      operation: OpenApiOperation;
      testCase: BoundaryTestCase;
    }>,
  ) => {
    if (!requested.length) {
      setMessage("Select an endpoint and at least one test case.");
      return;
    }
    if (requested.length > MAX_SUITE_CASES) {
      setMessage(`A suite can run at most ${MAX_SUITE_CASES} cases at a time.`);
      return;
    }
    const needsConfirmation =
      requested.length > 10 ||
      requested.some(
        ({ operation }) =>
          !["GET", "HEAD", "OPTIONS"].includes(operation.method),
      );
    if (
      needsConfirmation &&
      !window.confirm(
        `This suite will send ${requested.length} request(s) to the imported API. Non-read methods may modify data. Continue?`,
      )
    ) {
      setMessage("Run cancelled. No requests were sent.");
      return;
    }

    setIsRunning(true);
    setResults([]);
    setMessage("");
    const completed: OpenApiRunResult[] = [];
    for (const { operationKey: key, operation, testCase } of requested) {
      let request: ApiRequest | null = null;
      try {
        const requestConfiguration = createRequestConfiguration(operation.request);
        const defaultServer = specification?.servers[0];
        if (
          selectedServer &&
          defaultServer &&
          selectedServer !== defaultServer &&
          operation.request.url.startsWith(defaultServer)
        ) {
          requestConfiguration.url =
            selectedServer + operation.request.url.slice(defaultServer.length);
        }
        request = makeBoundaryRequest(requestConfiguration, testCase);
        const response = await executeRequest(request, {
          testCase: `${operation.id}: ${testCase.label}`,
        });
        completed.push({
          operationKey: key,
          testCase,
          request,
          response,
          error: null,
          outcome: responseOutcome(response),
        });
      } catch (error) {
        completed.push({
          operationKey: key,
          testCase,
          request,
          response: null,
          error: getErrorMessage(error),
          outcome: "errored",
        });
      }
      setResults([...completed]);
    }
    setIsRunning(false);
    setMessage("OpenAPI test run complete.");
  };

  const runSuite = () => {
    if (!specification) return;
    const requested: Array<{
      operationKey: string;
      operation: OpenApiOperation;
      testCase: BoundaryTestCase;
    }> = [];
    specification.operations.forEach((operation, index) => {
      const key = operationKey(index, operation);
      if (!selectedOperations.has(key)) return;
      operation.cases.forEach((testCase) => {
        if (selectedCases.has(caseKey(key, testCase))) {
          requested.push({ operationKey: key, operation, testCase });
        }
      });
    });
    void executeCases(requested);
  };

  const runCounts = results.reduce(
    (counts, result) => ({ ...counts, [result.outcome]: counts[result.outcome] + 1 }),
    { passed: 0, failed: 0, errored: 0 },
  );

  return (
    <section
      aria-labelledby="openapi-heading"
      className="mt-8 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80"
    >
      <div className="border-b border-slate-800 p-5">
        <div className="flex items-center gap-2 text-cyan-300">
          <FileUp size={17} />
          <h2 className="text-sm font-semibold text-slate-100" id="openapi-heading">
            OpenAPI / Swagger test generation
          </h2>
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          Import OpenAPI 3.0/3.1 or Swagger 2.0 JSON/YAML. The spec is treated as untrusted input.
          Imported API calls still pass through EdgeTest's outbound allowlist.
        </p>
        <div className="mt-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_auto_auto]">
          <label className="text-[10px] text-slate-500">
            Specification URL
            <input
              aria-label="OpenAPI specification URL"
              className="mt-1 h-10 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 font-mono text-xs text-slate-200 outline-none focus:border-cyan-400"
              onChange={(event) => setSpecUrl(event.target.value)}
              placeholder="https://api.example.test/openapi.json"
              type="url"
              value={specUrl}
            />
          </label>
          <button
            className="self-end rounded-lg border border-slate-700 px-4 py-2.5 text-xs font-semibold text-slate-300 hover:border-slate-500 disabled:opacity-50"
            disabled={isImporting}
            onClick={() => void importUrl()}
            type="button"
          >
            Import URL
          </button>
          <label className="self-end cursor-pointer rounded-lg border border-slate-700 px-4 py-2.5 text-center text-xs font-semibold text-slate-300 hover:border-slate-500">
            Upload JSON/YAML
            <input
              accept=".json,.yaml,.yml,application/json,application/yaml,text/yaml"
              aria-label="Upload OpenAPI specification"
              className="sr-only"
              disabled={isImporting}
              onChange={(event) => {
                void importFile(event.target.files?.[0]);
                event.target.value = "";
              }}
              type="file"
            />
          </label>
        </div>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          <label className="text-[10px] text-slate-500">
            Base URL (used only when the specification has no servers)
            <input
              aria-label="OpenAPI base URL"
              className="mt-1 h-10 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 font-mono text-xs text-slate-200 outline-none focus:border-cyan-400"
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="https://api.example.test/v1"
              type="url"
              value={baseUrl}
            />
          </label>
          {specification && specification.servers.length > 1 && (
            <label className="text-[10px] text-slate-500">
              API server
              <select
                aria-label="API server"
                className="mt-1 h-10 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 font-mono text-xs text-slate-200 outline-none focus:border-cyan-400"
                disabled={isRunning}
                onChange={(event) => setSelectedServer(event.target.value)}
                value={selectedServer}
              >
                {specification.servers.map((server) => (
                  <option key={server} value={server}>{server}</option>
                ))}
              </select>
            </label>
          )}
        </div>
        {isImporting && (
          <p className="mt-3 flex items-center gap-2 text-xs text-cyan-300">
            <LoaderCircle className="animate-spin" size={14} />
            Parsing specification…
          </p>
        )}
        {message && (
          <p aria-live="polite" className="mt-3 text-xs text-slate-400">
            {message}
          </p>
        )}
      </div>

      {specification && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-200">{specification.title}</p>
              <p className="mt-1 text-[10px] text-slate-500">
                {specification.specification_version} · version {specification.version} ·{" "}
                {specification.operations.length} operations
              </p>
            </div>
            <button
              className="inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-3 py-2 text-xs font-bold text-slate-950 hover:bg-emerald-300 disabled:opacity-50"
              disabled={isRunning || !selectedOperations.size}
              onClick={runSuite}
              type="button"
            >
              {isRunning ? <LoaderCircle className="animate-spin" size={14} /> : <Play size={13} fill="currentColor" />}
              Run selected suite
            </button>
          </div>
          {specification.warnings.map((warning) => (
            <p className="border-b border-amber-400/10 bg-amber-400/[0.03] px-5 py-2 text-[11px] text-amber-200/80" key={warning}>
              <AlertCircle className="mr-1 inline" size={12} />
              {warning}
            </p>
          ))}
          {specification.schemas.length > 0 && (
            <details className="border-b border-slate-800 px-5 py-3">
              <summary className="cursor-pointer text-xs font-semibold text-slate-300">
                Available schemas ({specification.schemas.length})
              </summary>
              <div className="mt-3 space-y-2">
                {specification.schemas.map((schema) => (
                  <details
                    className="rounded-lg border border-slate-800 bg-slate-950/50 p-3"
                    key={schema.name}
                  >
                    <summary className="cursor-pointer font-mono text-[11px] text-cyan-200">
                      {schema.name}
                    </summary>
                    <pre className="mt-2 max-h-48 overflow-auto text-[10px] text-slate-400">
                      {JSON.stringify(schema.schema, null, 2)}
                    </pre>
                  </details>
                ))}
              </div>
            </details>
          )}
          <ul className="divide-y divide-slate-800/80">
            {specification.operations.map((operation, index) => {
              const key = operationKey(index, operation);
              const selected = selectedOperations.has(key);
              const expanded = expandedOperations.has(key);
              const operationResults = results.filter((result) => result.operationKey === key);
              return (
                <li key={key}>
                  <div className="flex items-start gap-3 px-5 py-3">
                    <input
                      aria-label={`Select endpoint ${operation.method} ${operation.path}`}
                      checked={selected}
                      className="mt-1 size-4 accent-cyan-400"
                      disabled={isRunning}
                      onChange={() => toggleOperation(key, operation)}
                      type="checkbox"
                    />
                    <button
                      className="min-w-0 flex-1 text-left"
                      onClick={() =>
                        setExpandedOperations((previous) => {
                          const next = new Set(previous);
                          if (next.has(key)) next.delete(key);
                          else next.add(key);
                          return next;
                        })
                      }
                      type="button"
                    >
                      <span className="flex flex-wrap items-center gap-2">
                        <span className="rounded bg-cyan-400/10 px-2 py-1 font-mono text-[10px] font-bold text-cyan-200">
                          {operation.method}
                        </span>
                        <code className="break-all text-xs text-slate-200">{operation.path}</code>
                        <span className="text-[11px] text-slate-500">{operation.summary}</span>
                      </span>
                      <span className="mt-1 block text-[10px] text-slate-600">
                        {operation.id} · {operation.cases.length} generated cases ·{" "}
                        {operation.parameters.length} parameters
                      </span>
                    </button>
                    {selected && (
                      <button
                        aria-label={`Run selected cases for ${operation.method} ${operation.path}`}
                        className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:border-cyan-400/50 hover:text-cyan-200 disabled:opacity-50"
                        disabled={isRunning}
                        onClick={() =>
                          void executeCases(
                            operation.cases
                              .filter((testCase) =>
                                selectedCases.has(caseKey(key, testCase)),
                              )
                              .map((testCase) => ({
                                operationKey: key,
                                operation,
                                testCase,
                              })),
                          )
                        }
                        type="button"
                      >
                        <Play size={14} />
                      </button>
                    )}
                  </div>
                  {expanded && (
                    <div className="space-y-3 bg-slate-950/30 px-5 py-4 pl-12">
                      <div className="flex items-center justify-between text-[10px] text-slate-500">
                        <span>
                          {operation.request.url} · {operation.request.timeout}s timeout
                        </span>
                        {selected && (
                          <button
                            className="text-cyan-300 hover:text-cyan-200"
                            onClick={() =>
                              setSelectedCases((previous) => {
                                const next = new Set(previous);
                                const allSelected = operation.cases.every((testCase) =>
                                  next.has(caseKey(key, testCase)),
                                );
                                operation.cases.forEach((testCase) => {
                                  const id = caseKey(key, testCase);
                                  if (allSelected) next.delete(id);
                                  else next.add(id);
                                });
                                return next;
                              })
                            }
                            type="button"
                          >
                            Toggle all cases
                          </button>
                        )}
                      </div>
                      {operation.parameters.length > 0 && (
                        <div className="rounded-lg border border-slate-800 p-3">
                          <p className="mb-2 text-[10px] font-semibold uppercase text-slate-500">Parameters</p>
                          {operation.parameters.map((parameter) => (
                            <p className="py-1 font-mono text-[10px] text-slate-400" key={`${parameter.location}:${parameter.name}`}>
                              {parameter.location} · {parameter.name} · {parameter.required ? "required" : "optional"} · {JSON.stringify(parameter.schema)}
                            </p>
                          ))}
                        </div>
                      )}
                      {operation.request_body_schema !== null && (
                        <details className="rounded-lg border border-slate-800 p-3">
                          <summary className="cursor-pointer text-[10px] font-semibold uppercase text-slate-500">
                            Request body schema
                          </summary>
                          <pre className="mt-2 max-h-40 overflow-auto text-[10px] text-slate-400">
                            {JSON.stringify(operation.request_body_schema, null, 2)}
                          </pre>
                        </details>
                      )}
                      {operation.warnings.map((warning) => (
                        <p className="text-[10px] text-amber-200/80" key={warning}>
                          <AlertCircle className="mr-1 inline" size={12} />
                          {warning}
                        </p>
                      ))}
                      <ul className="space-y-1">
                        {operation.cases.map((testCase) => {
                          const id = caseKey(key, testCase);
                          const result = operationResults.find(
                            (item) => item.testCase.id === testCase.id,
                          );
                          return (
                            <li className="rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2" key={testCase.id}>
                              <div className="flex items-start gap-2">
                                <input
                                  aria-label={`Select case ${operation.method} ${operation.path} ${testCase.label}`}
                                  checked={selectedCases.has(id)}
                                  className="mt-0.5 size-3.5 accent-cyan-400"
                                  disabled={!selected || isRunning}
                                  onChange={() =>
                                    setSelectedCases((previous) => {
                                      const next = new Set(previous);
                                      if (next.has(id)) next.delete(id);
                                      else next.add(id);
                                      return next;
                                    })
                                  }
                                  type="checkbox"
                                />
                                <div className="min-w-0 flex-1">
                                  <p className="text-[11px] text-slate-300">{testCase.label}</p>
                                  <p className="mt-1 break-all font-mono text-[10px] text-slate-600">
                                    {testCase.location} · {testCase.input_name} ·{" "}
                                    {JSON.stringify(testCase.value).slice(0, 100)}
                                  </p>
                                  {result && (
                                    <details className="mt-2">
                                      <summary className="cursor-pointer text-[10px] text-cyan-300">
                                        {result.outcome} · {result.response?.status_code ?? "No status"}
                                        {result.response?.execution_time_ms != null
                                          ? ` · ${result.response.execution_time_ms.toFixed(1)} ms`
                                          : ""}
                                      </summary>
                                      <div className="mt-2 grid gap-2 lg:grid-cols-2">
                                        <pre className="max-h-40 overflow-auto rounded bg-slate-900 p-2 text-[9px] text-slate-400">
                                          {JSON.stringify(result.request, null, 2)}
                                        </pre>
                                        <pre className="max-h-40 overflow-auto rounded bg-slate-900 p-2 text-[9px] text-slate-400">
                                          {displayResult(result)}
                                        </pre>
                                      </div>
                                    </details>
                                  )}
                                </div>
                                {selected && !result && (
                                  <button
                                    aria-label={`Run case ${operation.method} ${operation.path} ${testCase.label}`}
                                    className="rounded border border-slate-700 px-2 py-1 text-[9px] text-slate-300 hover:border-cyan-400/50 hover:text-cyan-200 disabled:opacity-50"
                                    disabled={isRunning}
                                    onClick={() =>
                                      void executeCases([
                                        {
                                          operationKey: key,
                                          operation,
                                          testCase,
                                        },
                                      ])
                                    }
                                    type="button"
                                  >
                                    Run case
                                  </button>
                                )}
                                {result?.outcome === "passed" && (
                                  <CheckCircle2 className="text-emerald-300" size={14} />
                                )}
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {results.length > 0 && (
            <div aria-label="OpenAPI test summary" className="flex flex-wrap gap-4 border-t border-slate-800 px-5 py-3 text-xs">
              <span className="text-emerald-300">Passed {runCounts.passed}</span>
              <span className="text-rose-300">Failed {runCounts.failed}</span>
              <span className="text-amber-200">Errored {runCounts.errored}</span>
            </div>
          )}
          <div className="border-t border-slate-800 px-5 py-2 text-[10px] text-slate-600">
            <ShieldAlert className="mr-1 inline" size={12} />
            Imported server targets are checked by the outbound policy at execution time.
          </div>
        </>
      )}
    </section>
  );
}
