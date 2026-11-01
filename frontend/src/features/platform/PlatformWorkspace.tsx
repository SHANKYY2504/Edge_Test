import { useEffect, useState } from "react";
import {
  BarChart3,
  BrainCircuit,
  Download,
  FolderPlus,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
} from "lucide-react";

import {
  analyzeExecution,
  createCollection,
  deleteCollection,
  deleteSavedTest,
  executeSavedTest,
  exportExecutions,
  fetchAnalytics,
  fetchCollections,
  fetchFailures,
  fetchSavedTests,
  replayExecution,
  renameCollection,
  saveTest,
} from "../../services/apiClient";
import type {
  AiAnalysis,
  ApiRequest,
  ExecutionRecord,
  PlatformAnalytics,
  SavedTest,
  TestCollection,
} from "../../types/api";
import type { RequestConfiguration } from "../history/history";

const panelClass = "rounded-2xl border border-slate-800 bg-slate-900/80 p-5";
const inputClass =
  "rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-400";
const buttonClass =
  "inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-medium text-slate-300 transition hover:border-cyan-400/50 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50";

function requestFromConfiguration(
  configuration: RequestConfiguration,
): ApiRequest {
  const body = configuration.body.trim()
    ? (JSON.parse(configuration.body) as ApiRequest["body"])
    : undefined;
  return {
    method: configuration.method,
    url: configuration.url.trim(),
    headers: Object.fromEntries(
      configuration.headers
        .filter((pair) => pair.enabled && pair.key.trim())
        .map((pair) => [pair.key.trim(), pair.value]),
    ),
    query_params: Object.fromEntries(
      configuration.queryParams
        .filter((pair) => pair.enabled && pair.key.trim())
        .map((pair) => [pair.key.trim(), pair.value]),
    ),
    timeout: configuration.timeout,
    ...(body === undefined ? {} : { body }),
  };
}

function dateLabel(value: string): string {
  return new Date(value).toLocaleString();
}

function outcomeClass(outcome: ExecutionRecord["outcome"]): string {
  if (outcome === "passed") return "text-emerald-300";
  if (outcome === "failed") return "text-rose-300";
  return "text-amber-300";
}

function Metric({
  label,
  value,
  color = "text-white",
}: {
  label: string;
  value: number;
  color?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.15em] text-slate-500">
        {label}
      </p>
      <p className={`mt-2 text-2xl font-semibold ${color}`}>{value}</p>
    </div>
  );
}

export function PlatformWorkspace({
  configuration,
  generatedTestCases,
  onRestoreRequest,
}: {
  configuration: RequestConfiguration;
  generatedTestCases: Array<Record<string, unknown>>;
  onRestoreRequest: (request: ApiRequest) => void;
}) {
  const [analytics, setAnalytics] = useState<PlatformAnalytics | null>(null);
  const [collections, setCollections] = useState<TestCollection[]>([]);
  const [selectedCollection, setSelectedCollection] = useState("");
  const [savedTestsState, setSavedTestsState] = useState<{
    collectionId: string;
    tests: SavedTest[];
  }>({ collectionId: "", tests: [] });
  const [failures, setFailures] = useState<ExecutionRecord[]>([]);
  const [name, setName] = useState("");
  const [caseJsonDraft, setCaseJsonDraft] = useState<{
    source: Array<Record<string, unknown>>;
    text: string;
  } | null>(null);
  const caseJson =
    caseJsonDraft?.source === generatedTestCases
      ? caseJsonDraft.text
      : JSON.stringify(generatedTestCases, null, 2);
  const savedTests =
    savedTestsState.collectionId === selectedCollection
      ? savedTestsState.tests
      : [];
  const [endpointFilter, setEndpointFilter] = useState("");
  const [methodFilter, setMethodFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [errorFilter, setErrorFilter] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [comparison, setComparison] = useState<
    { original: ExecutionRecord; replay: ExecutionRecord } | null
  >(null);
  const [analysis, setAnalysis] = useState<{
    id: string;
    result: AiAnalysis;
  } | null>(null);

  const refresh = async () => {
    try {
      const [dashboard, allCollections, allFailures] = await Promise.all([
        fetchAnalytics(),
        fetchCollections(),
        fetchFailures(),
      ]);
      setAnalytics(dashboard);
      setCollections(allCollections);
      setFailures(allFailures);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load stored data.");
    }
  };

  useEffect(() => {
    let active = true;
    Promise.all([fetchAnalytics(), fetchCollections(), fetchFailures()])
      .then(([dashboard, allCollections, allFailures]) => {
        if (!active) return;
        setAnalytics(dashboard);
        setCollections(allCollections);
        setFailures(allFailures);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setMessage(error instanceof Error ? error.message : "Could not load stored data.");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedCollection) return;
    let active = true;
    fetchSavedTests(selectedCollection)
      .then((tests) => {
        if (active) {
          setSavedTestsState({ collectionId: selectedCollection, tests });
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setMessage(
            error instanceof Error ? error.message : "Could not load saved tests.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [selectedCollection]);

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The action could not be completed.");
    } finally {
      setBusy(false);
    }
  };

  const createNewCollection = () => {
    const collectionName = window.prompt("Name this test collection:");
    if (!collectionName?.trim()) return;
    void runAction(async () => {
      const created = await createCollection(collectionName.trim());
      setSelectedCollection(created.id);
      setMessage("Collection created.");
    });
  };

  const storeCurrentRequest = () => {
    if (!selectedCollection) {
      setMessage("Create or select a collection before saving.");
      return;
    }
    let request: ApiRequest;
    let cases: Array<Record<string, unknown>>;
    try {
      request = requestFromConfiguration(configuration);
      const parsed: unknown = JSON.parse(caseJson);
      if (
        !Array.isArray(parsed) ||
        !parsed.every((item) => typeof item === "object" && item !== null && !Array.isArray(item))
      ) {
        throw new Error("Test cases must be a JSON array of objects.");
      }
      cases = parsed as Array<Record<string, unknown>>;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Request or test-case JSON is invalid.");
      return;
    }
    const testName = name.trim() || `${request.method} ${request.url}`;
    void runAction(async () => {
      await saveTest(selectedCollection, {
        name: testName,
        request,
        test_cases: cases,
      });
      setName("");
      setMessage("Saved request and test cases. Secret values are redacted before storage.");
      setSavedTestsState({
        collectionId: selectedCollection,
        tests: await fetchSavedTests(selectedCollection),
      });
    });
  };

  const applyFailureFilters = () => {
    void runAction(async () => {
      setFailures(
        await fetchFailures({
          endpoint: endpointFilter,
          method: methodFilter,
          status_code: statusFilter,
          error_type: errorFilter,
        }),
      );
    });
  };

  const resultBlock = (record: ExecutionRecord) => (
    <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-[11px] leading-5 text-slate-400">
      {JSON.stringify(
        {
          status: record.status_code,
          outcome: record.outcome,
          duration_ms: record.duration_ms,
          response: record.response,
        },
        null,
        2,
      )}
    </pre>
  );

  return (
    <section className="mt-8 space-y-5" aria-label="Persistent test management">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="mb-1 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-cyan-300">
            <BarChart3 size={15} /> Persistent workspace
          </p>
          <h2 className="text-xl font-semibold text-white">Tests, failures & analytics</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          <a className={buttonClass} href={exportExecutions("json")} download>
            <Download size={14} /> Export JSON
          </a>
          <a className={buttonClass} href={exportExecutions("csv")} download>
            <Download size={14} /> Export CSV
          </a>
          <button className={buttonClass} disabled={busy} onClick={() => void refresh()} type="button">
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {message && (
        <p role="status" className="rounded-lg border border-slate-700 bg-slate-950/70 px-4 py-3 text-xs text-slate-300">
          {message}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <Metric label="Requests executed" value={analytics?.total_requests ?? 0} />
        <Metric label="Test cases run" value={analytics?.total_test_cases ?? 0} />
        <Metric label="Saved case definitions" value={analytics?.saved_test_case_definitions ?? 0} />
        <Metric label="Passed" value={analytics?.passed_tests ?? 0} color="text-emerald-300" />
        <Metric label="Failed" value={analytics?.failed_tests ?? 0} color="text-rose-300" />
        <Metric label="Errored" value={analytics?.errored_tests ?? 0} color="text-amber-300" />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <section className={panelClass} aria-labelledby="collections-heading">
          <div className="flex items-center justify-between gap-3">
            <h3 id="collections-heading" className="text-sm font-semibold">Test collections</h3>
            <button className={buttonClass} disabled={busy} onClick={createNewCollection} type="button">
              <FolderPlus size={14} /> New collection
            </button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <select
              aria-label="Select test collection"
              className={`${inputClass} min-w-48`}
              onChange={(event) => setSelectedCollection(event.target.value)}
              value={selectedCollection}
            >
              <option value="">Select a collection</option>
              {collections.map((collection) => (
                <option key={collection.id} value={collection.id}>{collection.name}</option>
              ))}
            </select>
            {selectedCollection && (
              <>
                <button
                  className={buttonClass}
                  onClick={() => {
                    const selected = collections.find((item) => item.id === selectedCollection);
                    if (!selected) return;
                    const nextName = window.prompt("Rename collection:", selected.name);
                    if (nextName?.trim()) {
                      void runAction(async () => {
                        await renameCollection(selected.id, nextName.trim());
                      });
                    }
                  }}
                  type="button"
                >
                  Rename
                </button>
                <button
                  className={`${buttonClass} hover:text-rose-200`}
                  onClick={() => {
                    if (!window.confirm("Delete this collection and its saved tests?")) return;
                    void runAction(async () => {
                      await deleteCollection(selectedCollection);
                      setSelectedCollection("");
                    });
                  }}
                  type="button"
                >
                  <Trash2 size={13} /> Delete
                </button>
              </>
            )}
          </div>

          {selectedCollection && (
            <div className="mt-4 space-y-2">
              <input
                aria-label="Saved test name"
                className={`${inputClass} w-full`}
                onChange={(event) => setName(event.target.value)}
                placeholder="Saved request name"
                value={name}
              />
              <textarea
                aria-label="Test cases JSON"
                className={`${inputClass} min-h-20 w-full font-mono text-xs`}
                onChange={(event) =>
                  setCaseJsonDraft({
                    source: generatedTestCases,
                    text: event.target.value,
                  })
                }
                placeholder="Generated test cases as a JSON array"
                value={caseJson}
              />
              <button className={buttonClass} disabled={busy} onClick={storeCurrentRequest} type="button">
                <Save size={14} /> Save current request
              </button>
              <div className="max-h-56 space-y-2 overflow-y-auto pt-2">
                {savedTests.map((test) => (
                  <div key={test.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium text-slate-200">{test.name}</p>
                      <p className="mt-1 text-[10px] text-slate-500">
                        {test.test_cases.length} case(s) · {test.request.method} · credentials redacted
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <button className={buttonClass} onClick={() => onRestoreRequest(test.request)} type="button">
                        Restore
                      </button>
                      <button
                        aria-label={`Run ${test.name}`}
                        className={buttonClass}
                        disabled={busy}
                        onClick={() => {
                          const confirmed =
                            ["GET", "HEAD", "OPTIONS"].includes(test.request.method) ||
                            window.confirm(
                              `${test.request.method} can modify data. Execute this saved request?`,
                            );
                          if (!confirmed) return;
                          void runAction(async () => {
                            const record = await executeSavedTest(test.id, confirmed);
                            setMessage(`Saved test completed: ${record.outcome}.`);
                          });
                        }}
                        type="button"
                      >
                        <Play size={13} /> Run
                      </button>
                      <button
                        aria-label={`Delete ${test.name}`}
                        className={buttonClass}
                        onClick={() => void runAction(async () => {
                          await deleteSavedTest(test.id);
                          setSavedTestsState({
                            collectionId: selectedCollection,
                            tests: await fetchSavedTests(selectedCollection),
                          });
                        })}
                        type="button"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                ))}
                {savedTests.length === 0 && (
                  <p className="py-4 text-center text-xs text-slate-500">No saved requests in this collection.</p>
                )}
              </div>
              <p className="text-[10px] leading-4 text-slate-500">
                Secret-like headers, query values, and JSON fields are redacted in persistent storage. Re-enter secrets before replaying.
              </p>
            </div>
          )}
        </section>

        <section className={panelClass} aria-labelledby="trends-heading">
          <h3 id="trends-heading" className="text-sm font-semibold">Execution trends</h3>
          <div className="mt-4 space-y-2">
            {(analytics?.trends ?? []).map((trend) => (
              <div key={trend.date} className="grid grid-cols-[90px_1fr_45px] items-center gap-3 text-xs">
                <span className="font-mono text-slate-500">{trend.date}</span>
                <div className="flex h-3 overflow-hidden rounded-full bg-slate-800">
                  <span
                    className="bg-emerald-400/80"
                    style={{ width: `${trend.total ? (trend.passed / trend.total) * 100 : 0}%` }}
                  />
                  <span className="flex-1 bg-rose-400/80" />
                </div>
                <span className="text-right text-slate-400">{trend.total}</span>
              </div>
            ))}
            {analytics?.trends.length === 0 && (
              <p className="py-5 text-center text-xs text-slate-500">No stored executions yet.</p>
            )}
          </div>
          <h4 className="mt-6 text-xs font-semibold text-slate-300">Most frequently failing endpoints</h4>
          <div className="mt-2 space-y-2">
            {(analytics?.failing_endpoints ?? []).map((item) => (
              <div key={`${item.method}-${item.endpoint}`} className="flex justify-between gap-3 rounded-lg bg-slate-950/50 px-3 py-2 text-[11px]">
                <span className="truncate font-mono text-slate-400">{item.method} {item.endpoint}</span>
                <span className="shrink-0 text-rose-300">{item.failures} failure(s)</span>
              </div>
            ))}
            {analytics?.failing_endpoints.length === 0 && (
              <p className="text-xs text-slate-500">No failed endpoints recorded.</p>
            )}
          </div>
        </section>
      </div>

      <section className={panelClass} aria-labelledby="failures-heading">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 id="failures-heading" className="text-sm font-semibold">Failure repository</h3>
            <p className="mt-1 text-[11px] text-slate-500">Failed HTTP responses and transport errors are saved automatically.</p>
          </div>
          <button className={buttonClass} onClick={applyFailureFilters} type="button">
            Apply filters
          </button>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <input aria-label="Filter by endpoint" className={inputClass} onChange={(event) => setEndpointFilter(event.target.value)} placeholder="Endpoint contains…" value={endpointFilter} />
          <select aria-label="Filter by method" className={inputClass} onChange={(event) => setMethodFilter(event.target.value)} value={methodFilter}>
            <option value="">Any method</option>
            {["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"].map((method) => <option key={method}>{method}</option>)}
          </select>
          <input aria-label="Filter by status code" className={inputClass} onChange={(event) => setStatusFilter(event.target.value)} placeholder="HTTP status code" value={statusFilter} />
          <input aria-label="Filter by error type" className={inputClass} onChange={(event) => setErrorFilter(event.target.value)} placeholder="Error category" value={errorFilter} />
        </div>

        <div className="mt-4 space-y-3">
          {failures.map((failure) => (
            <article key={failure.id} className="rounded-xl border border-slate-800 bg-slate-950/45 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-all font-mono text-xs text-slate-200">{failure.method} {failure.endpoint}</p>
                  <p className="mt-1 text-[10px] text-slate-500">
                    {dateLabel(failure.created_at)} · status {failure.status_code ?? "none"} · <span className={outcomeClass(failure.outcome)}>{failure.error_category ?? "HTTP response"}</span>
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    className={buttonClass}
                    onClick={() => {
                      const confirmed =
                        ["GET", "HEAD", "OPTIONS"].includes(failure.method) ||
                        window.confirm(
                          `${failure.method} can modify data. Replay the sanitized stored request?`,
                        );
                      if (!confirmed) return;
                      void runAction(async () => {
                        setComparison(await replayExecution(failure.id, confirmed));
                        setAnalysis(null);
                      });
                    }}
                    type="button"
                  >
                    <RotateCcw size={13} /> Replay
                  </button>
                  <button
                    className={buttonClass}
                    onClick={() => {
                      if (!window.confirm("Send the sanitized request and response to the configured AI provider for analysis?")) return;
                      void runAction(async () => {
                        setAnalysis({ id: failure.id, result: await analyzeExecution(failure.id) });
                      });
                    }}
                    type="button"
                  >
                    <BrainCircuit size={13} /> AI analysis
                  </button>
                </div>
              </div>
              <details className="mt-3">
                <summary className="cursor-pointer text-[11px] text-cyan-200">Inspect sanitized request and response</summary>
                <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-[10px] leading-5 text-slate-500">
                  {JSON.stringify({ request: failure.request, response: failure.response }, null, 2)}
                </pre>
              </details>
              {comparison?.original.id === failure.id && (
                <div className="mt-4 grid gap-3 lg:grid-cols-2" aria-label="Replay comparison">
                  <div><p className="mb-2 text-[10px] uppercase tracking-wider text-slate-500">Original</p>{resultBlock(comparison.original)}</div>
                  <div><p className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-wider text-slate-500"><RotateCcw size={12} /> Replay</p>{resultBlock(comparison.replay)}</div>
                </div>
              )}
              {analysis?.id === failure.id && (
                <div className="mt-4 rounded-lg border border-violet-400/20 bg-violet-400/[0.04] p-4">
                  <p className="text-xs font-semibold text-violet-200">AI recommendations · not a guaranteed diagnosis</p>
                  <p aria-label="AI error summary" className="mt-3 text-xs font-medium text-slate-200">{analysis.result.error_summary}</p>
                  <p className="mt-2 text-xs text-slate-200">{analysis.result.possible_root_cause}</p>
                  <p className="mt-2 text-xs leading-5 text-slate-400">{analysis.result.explanation}</p>
                  {[
                    ["Debugging steps", analysis.result.debugging_steps],
                    ["Potential API issues", analysis.result.potential_issues],
                    ["Suggested corrections / test cases", analysis.result.suggested_corrections],
                  ].map(([label, items]) => (
                    <div key={label as string} className="mt-3">
                      <p className="text-[10px] font-semibold uppercase text-slate-500">{label as string}</p>
                      <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-slate-400">
                        {(items as string[]).map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </article>
          ))}
          {failures.length === 0 && (
            <p className="py-6 text-center text-xs text-slate-500">No failures match these filters.</p>
          )}
        </div>
      </section>
    </section>
  );
}
