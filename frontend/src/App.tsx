import { useState } from "react";
import {
  Activity,
  AlertCircle,
  Braces,
  Check,
  ChevronDown,
  Clock3,
  Code2,
  CornerDownLeft,
  FileJson2,
  FlaskConical,
  History,
  KeyRound,
  LoaderCircle,
  Plus,
  Play,
  Trash2,
} from "lucide-react";

import { ApiClientError, executeRequest } from "./services/apiClient";
import { BoundaryTester } from "./features/boundary/BoundaryTester";
import { OpenApiTester } from "./features/openapi/OpenApiTester";
import { PlatformWorkspace } from "./features/platform/PlatformWorkspace";
import {
  makeHistoryEntry,
  redactConfiguration,
  type HistoryEntry,
  type RequestConfiguration,
} from "./features/history/history";
import {
  HTTP_METHODS,
  type ApiRequest,
  type ApiResponse,
  type HttpMethod,
  type KeyValuePair,
} from "./types/api";

type RequestTab = "Headers" | "Query Params" | "Body";
type ResponseTab = "Body" | "Headers";

const requestTabs: Array<{
  label: RequestTab;
  icon: typeof KeyRound;
}> = [
  { label: "Headers", icon: KeyRound },
  { label: "Query Params", icon: Code2 },
  { label: "Body", icon: FileJson2 },
];

const EMPTY_RESPONSE: ApiResponse = {
  status_code: null,
  headers: {},
  body: null,
  content_type: null,
  is_json: false,
  execution_time_ms: null,
  error: null,
};

const DEFAULT_HEADERS: KeyValuePair[] = [
  {
    id: crypto.randomUUID(),
    key: "Accept",
    value: "application/json",
    enabled: true,
  },
];

function createPair(): KeyValuePair {
  return {
    id: crypto.randomUUID(),
    key: "",
    value: "",
    enabled: true,
  };
}

function createDefaultConfiguration(): RequestConfiguration {
  return {
    method: "GET",
    url: "http://127.0.0.1:8001/users",
    headers: DEFAULT_HEADERS.map((header) => ({ ...header })),
    queryParams: [],
    body: "",
    timeout: 5,
  };
}

function pairsToRecord(pairs: KeyValuePair[]): Record<string, string> {
  return Object.fromEntries(
    pairs
      .filter((pair) => pair.enabled && pair.key.trim())
      .map((pair) => [pair.key.trim(), pair.value]),
  );
}

function formatBody(body: ApiResponse["body"], isJson: boolean): string {
  if (body === null) return "";
  if (isJson) return JSON.stringify(body, null, 2);
  return typeof body === "string" ? body : JSON.stringify(body, null, 2);
}

function responseTone(response: ApiResponse): "success" | "error" | "neutral" {
  if (response.error || (response.status_code !== null && response.status_code >= 400)) {
    return "error";
  }
  if (response.status_code !== null && response.status_code < 400) {
    return "success";
  }
  return "neutral";
}

function statusLabel(response: ApiResponse): string {
  if (response.error?.code === "timeout" || response.error?.code === "backend_timeout") {
    return "Timed out";
  }
  if (
    response.error &&
    ["network_error", "connection_error", "dns_resolution_failed", "transport_error"].includes(
      response.error.code,
    )
  ) {
    return "Network error";
  }
  if (response.error && response.status_code === null) return "Request failed";
  if (response.status_code === null) return "No response";
  return `${response.status_code} ${statusText(response.status_code)}`;
}

function statusText(status: number): string {
  return (
    new Map([
      [200, "OK"],
      [201, "Created"],
      [202, "Accepted"],
      [204, "No Content"],
      [400, "Bad Request"],
      [401, "Unauthorized"],
      [403, "Forbidden"],
      [404, "Not Found"],
      [408, "Request Timeout"],
      [422, "Unprocessable Entity"],
      [429, "Too Many Requests"],
      [500, "Internal Server Error"],
      [502, "Bad Gateway"],
      [503, "Service Unavailable"],
    ]).get(status) ?? "Response"
  );
}

function failureResponse(error: unknown): ApiResponse {
  const apiError =
    error instanceof ApiClientError
      ? { code: error.code, message: error.message }
      : { code: "network_error", message: "The request could not be completed." };
  return { ...EMPTY_RESPONSE, error: apiError };
}

function KeyValueEditor({
  title,
  rows,
  onChange,
  emptyText,
}: {
  title: string;
  rows: KeyValuePair[];
  onChange: (rows: KeyValuePair[]) => void;
  emptyText: string;
}) {
  const updateRow = (id: string, updates: Partial<KeyValuePair>) => {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...updates } : row)));
  };

  return (
    <div>
      <div className="grid grid-cols-[34px_minmax(0,1fr)_minmax(0,1.3fr)_38px] gap-3 px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
        <span aria-label={`${title} enabled`} />
        <span>Key</span>
        <span>Value</span>
        <span />
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-slate-500">{emptyText}</p>
      ) : (
        <div className="space-y-2">
          {rows.map((row, index) => (
            <div
              className="grid grid-cols-[34px_minmax(0,1fr)_minmax(0,1.3fr)_38px] items-center gap-3 rounded-xl border border-slate-800 bg-slate-950/60 p-2"
              key={row.id}
            >
              <input
                aria-label={`Enable ${title.toLowerCase()} ${index + 1}`}
                checked={row.enabled}
                className="mx-auto size-4 accent-cyan-400"
                onChange={(event) => updateRow(row.id, { enabled: event.target.checked })}
                type="checkbox"
              />
              <input
                aria-label={`${title} key ${index + 1}`}
                className="min-w-0 bg-transparent py-2 text-sm text-slate-200 outline-none placeholder:text-slate-600"
                onChange={(event) => updateRow(row.id, { key: event.target.value })}
                placeholder={title === "Headers" ? "Authorization" : "page"}
                value={row.key}
              />
              <input
                aria-label={`${title} value ${index + 1}`}
                className="min-w-0 bg-transparent py-2 text-sm text-slate-300 outline-none placeholder:text-slate-600"
                onChange={(event) => updateRow(row.id, { value: event.target.value })}
                placeholder={title === "Headers" ? "Bearer …" : "1"}
                value={row.value}
              />
              <button
                aria-label={`Delete ${title.toLowerCase()} row ${index + 1}`}
                className="rounded-lg p-2 text-slate-500 transition hover:bg-rose-400/10 hover:text-rose-300"
                onClick={() => onChange(rows.filter((item) => item.id !== row.id))}
                type="button"
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        className="mt-4 inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-cyan-300 transition hover:bg-cyan-400/10"
        onClick={() => onChange([...rows, createPair()])}
        type="button"
      >
        <Plus size={15} />
        Add {title === "Query Params" ? "parameter" : "row"}
      </button>
    </div>
  );
}

function RequestComposer({
  configuration,
  setConfiguration,
  activeTab,
  setActiveTab,
  onSend,
  isLoading,
  jsonError,
}: {
  configuration: RequestConfiguration;
  setConfiguration: (configuration: RequestConfiguration) => void;
  activeTab: RequestTab;
  setActiveTab: (tab: RequestTab) => void;
  onSend: () => void;
  isLoading: boolean;
  jsonError: string | null;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80 shadow-2xl shadow-black/20">
      <div className="flex flex-col gap-3 border-b border-slate-800 p-4 sm:flex-row">
        <label className="relative shrink-0">
          <span className="sr-only">HTTP method</span>
          <select
            aria-label="HTTP method"
            className="h-12 w-32 appearance-none rounded-xl border border-slate-700 bg-slate-950 px-4 pr-9 font-mono text-sm font-bold text-emerald-300 outline-none transition focus:border-cyan-400"
            onChange={(event) =>
              setConfiguration({
                ...configuration,
                method: event.target.value as HttpMethod,
              })
            }
            value={configuration.method}
          >
            {HTTP_METHODS.map((method) => (
              <option key={method} value={method}>
                {method}
              </option>
            ))}
          </select>
          <ChevronDown
            aria-hidden="true"
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-500"
            size={15}
          />
        </label>
        <label className="min-w-0 flex-1">
          <span className="sr-only">Request URL</span>
          <input
            aria-label="Request URL"
            className="h-12 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 font-mono text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-cyan-400"
            onChange={(event) =>
              setConfiguration({ ...configuration, url: event.target.value })
            }
            placeholder="https://api.example.com/resource"
            type="url"
            value={configuration.url}
          />
        </label>
        <button
          className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-cyan-400 px-5 text-sm font-bold text-slate-950 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isLoading}
          onClick={onSend}
          type="button"
        >
          {isLoading ? (
            <LoaderCircle className="animate-spin" size={17} />
          ) : (
            <Play fill="currentColor" size={15} />
          )}
          {isLoading ? "Sending" : "Send"}
        </button>
      </div>

      <div className="flex items-center justify-between border-b border-slate-800 px-4">
        <div aria-label="Request options" className="flex gap-1" role="tablist">
          {requestTabs.map(({ label, icon: Icon }) => (
            <button
              aria-selected={activeTab === label}
              className={`inline-flex items-center gap-2 border-b-2 px-3 py-3 text-xs font-semibold transition ${
                activeTab === label
                  ? "border-cyan-400 text-cyan-300"
                  : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
              key={label}
              onClick={() => setActiveTab(label)}
              role="tab"
              type="button"
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Timeout
          <input
            aria-label="Timeout seconds"
            className="w-16 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-right font-mono text-xs text-slate-200 outline-none focus:border-cyan-400"
            max={30}
            min={0.1}
            onChange={(event) =>
              setConfiguration({
                ...configuration,
                timeout: Number(event.target.value),
              })
            }
            step={0.1}
            type="number"
            value={configuration.timeout}
          />
          sec
        </label>
      </div>

      <div className="min-h-52 p-4">
        {activeTab === "Headers" && (
          <KeyValueEditor
            emptyText="No headers added. Add one to customize your request."
            onChange={(headers) => setConfiguration({ ...configuration, headers })}
            rows={configuration.headers}
            title="Headers"
          />
        )}
        {activeTab === "Query Params" && (
          <KeyValueEditor
            emptyText="No query parameters added."
            onChange={(queryParams) =>
              setConfiguration({ ...configuration, queryParams })
            }
            rows={configuration.queryParams}
            title="Query Params"
          />
        )}
        {activeTab === "Body" && (
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
                JSON
              </span>
              <span className="font-mono text-[10px] text-slate-600">application/json</span>
            </div>
            <textarea
              aria-label="JSON request body"
              className={`min-h-40 w-full resize-y rounded-xl border bg-slate-950 p-4 font-mono text-sm leading-6 text-slate-200 outline-none placeholder:text-slate-600 ${
                jsonError
                  ? "border-rose-500/60 focus:border-rose-400"
                  : "border-slate-800 focus:border-cyan-400"
              }`}
              onChange={(event) =>
                setConfiguration({ ...configuration, body: event.target.value })
              }
              placeholder={'{\n  "name": "Ada Lovelace"\n}'}
              spellCheck={false}
              value={configuration.body}
            />
            {jsonError && (
              <p className="mt-2 flex items-center gap-2 text-xs text-rose-300">
                <AlertCircle size={14} />
                {jsonError}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function ResponsePanel({
  response,
  activeTab,
  setActiveTab,
  hasSentRequest,
}: {
  response: ApiResponse;
  activeTab: ResponseTab;
  setActiveTab: (tab: ResponseTab) => void;
  hasSentRequest: boolean;
}) {
  const tone = responseTone(response);
  const body = formatBody(response.body, response.is_json);
  const tabs: ResponseTab[] = ["Body", "Headers"];

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80">
      <div className="flex min-h-[68px] flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 py-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-slate-100">Response</h2>
          {hasSentRequest && (
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-bold ${
                tone === "success"
                  ? "bg-emerald-400/10 text-emerald-300"
                  : tone === "error"
                    ? "bg-rose-400/10 text-rose-300"
                    : "bg-slate-800 text-slate-300"
              }`}
            >
              {tone === "success" ? <Check size={12} /> : <AlertCircle size={12} />}
              {statusLabel(response)}
            </span>
          )}
        </div>
        {hasSentRequest && (
          <div className="flex items-center gap-4 text-xs text-slate-400">
            {response.execution_time_ms !== null && (
              <span className="inline-flex items-center gap-1.5">
                <Clock3 size={13} />
                {response.execution_time_ms.toFixed(1)} ms
              </span>
            )}
            {response.status_code !== null && (
              <span className="font-mono text-slate-500">
                {response.content_type ?? "unknown content type"}
              </span>
            )}
          </div>
        )}
      </div>

      {!hasSentRequest ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="flex size-12 items-center justify-center rounded-2xl border border-slate-800 bg-slate-950 text-slate-600">
            <Activity size={21} />
          </div>
          <p className="text-sm font-medium text-slate-300">Response will appear here</p>
          <p className="max-w-sm text-xs leading-5 text-slate-500">
            Configure your request and press Send to see the response status, timing,
            body, and headers.
          </p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-4 border-b border-slate-800 px-5">
            {tabs.map((tab) => (
              <button
                aria-selected={activeTab === tab}
                className={`border-b-2 py-3 text-xs font-semibold ${
                  activeTab === tab
                    ? "border-cyan-400 text-slate-100"
                    : "border-transparent text-slate-500 hover:text-slate-300"
                }`}
                key={tab}
                onClick={() => setActiveTab(tab)}
                role="tab"
                type="button"
              >
                {tab}
              </button>
            ))}
          </div>
          <div className="min-h-56 p-5">
            {response.error && (
              <div className="mb-4 flex gap-3 rounded-xl border border-rose-400/20 bg-rose-400/5 p-3 text-sm text-rose-200">
                <AlertCircle className="mt-0.5 shrink-0 text-rose-300" size={16} />
                <div>
                  <p className="font-semibold">{response.error.code.replaceAll("_", " ")}</p>
                  <p className="mt-1 text-xs leading-5 text-rose-200/75">
                    {response.error.message}
                  </p>
                </div>
              </div>
            )}
            {activeTab === "Body" ? (
              body ? (
                <pre className="max-h-[440px] overflow-auto rounded-xl bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-300">
                  <code>{body}</code>
                </pre>
              ) : (
                <p className="py-12 text-center text-xs text-slate-500">
                  This response has no body.
                </p>
              )
            ) : Object.keys(response.headers).length ? (
              <div className="overflow-hidden rounded-xl border border-slate-800">
                {Object.entries(response.headers).map(([key, value]) => (
                  <div
                    className="grid grid-cols-[minmax(120px,0.7fr)_minmax(0,1.3fr)] gap-4 border-b border-slate-800 px-4 py-2.5 text-xs last:border-b-0"
                    key={key}
                  >
                    <span className="break-all font-mono text-cyan-200/80">{key}</span>
                    <span className="break-all font-mono text-slate-400">{value}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-12 text-center text-xs text-slate-500">
                No response headers.
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function App() {
  const [configuration, setConfiguration] = useState(createDefaultConfiguration);
  const [generatedTestCases, setGeneratedTestCases] = useState<
    Array<Record<string, unknown>>
  >([]);
  const [activeRequestTab, setActiveRequestTab] = useState<RequestTab>("Headers");
  const [activeResponseTab, setActiveResponseTab] = useState<ResponseTab>("Body");
  const [response, setResponse] = useState<ApiResponse>(EMPTY_RESPONSE);
  const [hasSentRequest, setHasSentRequest] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [jsonError, setJsonError] = useState<string | null>(null);

  const sendRequest = async () => {
    let parsedBody: ApiRequest["body"];
    if (configuration.body.trim()) {
      try {
        parsedBody = JSON.parse(configuration.body) as ApiRequest["body"];
      } catch {
        setActiveRequestTab("Body");
        setJsonError("Enter valid JSON before sending.");
        return;
      }
    }

    if (!configuration.url.trim()) {
      setActiveRequestTab("Headers");
      setJsonError(null);
      setResponse({
        ...EMPTY_RESPONSE,
        error: {
          code: "validation_error",
          message: "Enter a request URL.",
        },
      });
      setHasSentRequest(true);
      return;
    }
    if (!Number.isFinite(configuration.timeout) || configuration.timeout < 0.1 || configuration.timeout > 30) {
      setJsonError(null);
      setResponse({
        ...EMPTY_RESPONSE,
        error: {
          code: "validation_error",
          message: "Timeout must be between 0.1 and 30 seconds.",
        },
      });
      setHasSentRequest(true);
      return;
    }

    setJsonError(null);
    setIsLoading(true);
    setHasSentRequest(true);
    setResponse(EMPTY_RESPONSE);
    const request: ApiRequest = {
      url: configuration.url.trim(),
      method: configuration.method,
      headers: pairsToRecord(configuration.headers),
      query_params: pairsToRecord(configuration.queryParams),
      timeout: configuration.timeout,
      ...(parsedBody !== undefined ? { body: parsedBody } : {}),
    };

    let result: ApiResponse;
    try {
      result = await executeRequest(request);
    } catch (error) {
      result = failureResponse(error);
    } finally {
      setIsLoading(false);
    }

    setResponse(result);
    setHistory((previous) =>
      [makeHistoryEntry(configuration, result), ...previous].slice(0, 30),
    );
  };

  const restoreHistoryEntry = (entry: HistoryEntry) => {
    setConfiguration(redactConfiguration(entry.configuration));
    setResponse(EMPTY_RESPONSE);
    setHasSentRequest(false);
    setJsonError(null);
    setActiveRequestTab("Headers");
  };

  const restorePersistentRequest = (request: ApiRequest) => {
    setConfiguration({
      method: request.method,
      url: request.url,
      headers: Object.entries(request.headers).map(([key, value]) => ({
        id: crypto.randomUUID(),
        key,
        value,
        enabled: true,
      })),
      queryParams: Object.entries(request.query_params).map(([key, value]) => ({
        id: crypto.randomUUID(),
        key,
        value,
        enabled: true,
      })),
      body:
        request.body === undefined || request.body === null
          ? ""
          : JSON.stringify(request.body, null, 2),
      timeout: request.timeout,
    });
    setResponse(EMPTY_RESPONSE);
    setHasSentRequest(false);
    setJsonError(null);
    setActiveRequestTab("Headers");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const formatHistoryTime = (timestamp: string) =>
    new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(timestamp));

  return (
    <main className="min-h-screen bg-[#080d17] text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(8,145,178,0.09),transparent_35%)]" />
      <div className="relative mx-auto flex min-h-screen max-w-[1600px]">
        <aside className="hidden w-[260px] shrink-0 flex-col border-r border-slate-800/80 bg-slate-950/30 p-5 xl:flex">
          <a className="flex items-center gap-3" href="/" aria-label="EdgeTest home">
            <div className="flex size-10 items-center justify-center rounded-xl bg-cyan-400 text-slate-950 shadow-lg shadow-cyan-500/10">
              <Braces size={22} strokeWidth={2.2} />
            </div>
            <div>
              <span className="block text-base font-bold tracking-tight">EdgeTest</span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.16em] text-slate-500">
                API workspace
              </span>
            </div>
          </a>

          <div className="mt-10">
            <p className="px-2 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-600">
              Workspace
            </p>
            <div className="mt-3 flex items-center gap-3 rounded-xl border border-cyan-400/15 bg-cyan-400/[0.06] px-3 py-3 text-sm font-medium text-cyan-100">
              <FlaskConical size={16} />
              API Request
              <span className="ml-auto size-1.5 rounded-full bg-cyan-300" />
            </div>
          </div>

          <div className="mt-9 flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between px-2">
              <p className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-slate-600">
                <History size={13} />
                Recent requests
              </p>
              {history.length > 0 && (
                <button
                  aria-label="Clear request history"
                  className="rounded-md p-1 text-slate-600 transition hover:text-rose-300"
                  onClick={() => setHistory([])}
                  title="Clear history"
                  type="button"
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>
            {history.length === 0 ? (
              <div className="mt-4 rounded-xl border border-dashed border-slate-800 px-3 py-5 text-center">
                <p className="text-xs text-slate-500">No requests yet</p>
                <p className="mt-1 text-[10px] leading-4 text-slate-600">
                  Requests in this session will show here.
                </p>
              </div>
            ) : (
              <ul className="mt-3 space-y-1 overflow-y-auto">
                {history.map((entry) => (
                  <li key={entry.id}>
                    <button
                      className="group w-full rounded-xl px-3 py-3 text-left transition hover:bg-slate-900"
                      onClick={() => restoreHistoryEntry(entry)}
                      title="Restore request configuration"
                      type="button"
                    >
                      <span className="flex items-center gap-2">
                        <span className="w-10 font-mono text-[10px] font-bold text-emerald-300">
                          {entry.method}
                        </span>
                        <span className="truncate font-mono text-[11px] text-slate-400 group-hover:text-slate-200">
                          {entry.url}
                        </span>
                      </span>
                      <span className="mt-2 flex items-center justify-between text-[10px] text-slate-600">
                        <span>{formatHistoryTime(entry.timestamp)}</span>
                        <span className={entry.status && entry.status >= 400 ? "text-rose-300" : ""}>
                          {entry.status ?? "Failed"}
                          {entry.duration !== null ? ` · ${entry.duration.toFixed(0)} ms` : ""}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="mt-6 rounded-xl border border-slate-800 bg-slate-900/60 p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-slate-300">
              <span className="size-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.45)]" />
              Local demo target
            </div>
            <p className="mt-2 truncate font-mono text-[10px] text-slate-500">
              127.0.0.1:8001
            </p>
            <p className="mt-1 text-[10px] leading-4 text-slate-600">
              Local targets only · SQLite history
            </p>
          </div>
        </aside>

        <div className="min-w-0 flex-1">
          <header className="flex items-center justify-between border-b border-slate-800/80 px-5 py-4 sm:px-8">
            <div className="flex items-center gap-3 xl:hidden">
              <div className="flex size-9 items-center justify-center rounded-xl bg-cyan-400 text-slate-950">
                <Braces size={20} />
              </div>
              <span className="font-bold">EdgeTest</span>
            </div>
            <div className="hidden xl:block">
              <p className="text-xs text-slate-500">Workspace / API Request</p>
            </div>
            <div className="flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900/70 px-3 py-1.5 text-[10px] font-medium text-slate-400">
              <span className="size-1.5 rounded-full bg-emerald-400" />
              API TESTING PLATFORM
            </div>
          </header>

          <section className="border-b border-slate-800/70 px-5 py-3 xl:hidden sm:px-8">
            <details className="group">
              <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-slate-400">
                <span className="flex items-center gap-2">
                  <History size={14} />
                  Recent requests
                  <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400">
                    {history.length}
                  </span>
                </span>
                {history.length > 0 && (
                  <button
                    aria-label="Clear request history"
                    className="rounded-md p-1 text-slate-500 hover:text-rose-300"
                    onClick={(event) => {
                      event.preventDefault();
                      setHistory([]);
                    }}
                    type="button"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </summary>
              <ul className="mt-3 max-h-48 space-y-1 overflow-y-auto">
                {history.map((entry) => (
                  <li key={entry.id}>
                    <button
                      className="w-full rounded-lg bg-slate-900/70 px-3 py-2 text-left"
                      onClick={() => restoreHistoryEntry(entry)}
                      type="button"
                    >
                      <span className="mr-2 font-mono text-[10px] font-bold text-emerald-300">
                        {entry.method}
                      </span>
                      <span className="break-all font-mono text-[11px] text-slate-300">
                        {entry.url}
                      </span>
                      <span className="ml-2 text-[10px] text-slate-500">
                        {entry.status ?? "Failed"}
                      </span>
                    </button>
                  </li>
                ))}
                {history.length === 0 && (
                  <li className="py-2 text-xs text-slate-600">No requests in this session.</li>
                )}
              </ul>
            </details>
          </section>

          <div className="mx-auto max-w-5xl px-5 pb-10 pt-9 sm:px-8 sm:pt-12">
            <div className="mb-7">
              <p className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">
                <span className="h-px w-5 bg-cyan-400/70" />
                API testing workspace
              </p>
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div>
                  <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
                    Request builder
                  </h1>
                  <p className="mt-2 max-w-xl text-sm leading-6 text-slate-400">
                    Compose an HTTP request, send it through the guarded EdgeTest
                    engine, and inspect the response.
                  </p>
                </div>
                <div className="hidden items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-[11px] text-slate-500 sm:flex">
                  <CornerDownLeft size={13} />
                  Session-only history
                </div>
              </div>
            </div>

            <RequestComposer
              activeTab={activeRequestTab}
              configuration={configuration}
              isLoading={isLoading}
              jsonError={jsonError}
              onSend={() => void sendRequest()}
              setActiveTab={setActiveRequestTab}
              setConfiguration={setConfiguration}
            />

            <BoundaryTester
              configuration={configuration}
              onGeneratedCases={setGeneratedTestCases}
            />

            <OpenApiTester onGeneratedCases={setGeneratedTestCases} />

            <PlatformWorkspace
              configuration={configuration}
              generatedTestCases={generatedTestCases}
              onRestoreRequest={restorePersistentRequest}
            />

            <div className="mt-7 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Activity className="text-cyan-300" size={16} />
                <h2 className="text-sm font-semibold">Latest result</h2>
              </div>
              {hasSentRequest && !isLoading && responseTone(response) === "success" && (
                <span className="flex items-center gap-1.5 text-[11px] text-emerald-300">
                  <Check size={13} />
                  Response received
                </span>
              )}
              {isLoading && (
                <span className="flex items-center gap-2 text-[11px] text-cyan-300">
                  <LoaderCircle className="animate-spin" size={13} />
                  Sending request
                </span>
              )}
            </div>
            <div className="mt-3">
              <ResponsePanel
                activeTab={activeResponseTab}
                hasSentRequest={hasSentRequest}
                response={response}
                setActiveTab={setActiveResponseTab}
              />
            </div>

            <footer className="mt-6 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-600">
              <span className="flex items-center gap-1.5">
                <span className="size-1.5 rounded-full bg-emerald-400" />
                Outbound allowlist and response limits enabled
              </span>
              <span>Persistent request records redact secret-like values</span>
            </footer>
          </div>
        </div>
      </div>
    </main>
  );
}

export default App;
