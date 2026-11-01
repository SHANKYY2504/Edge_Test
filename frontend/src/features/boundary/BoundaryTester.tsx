import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  FlaskConical,
  LoaderCircle,
  Play,
  Plus,
  Trash2,
} from "lucide-react";

import {
  ApiClientError,
  executeRequest,
  generateBoundaryCases,
} from "../../services/apiClient";
import type {
  ApiRequest,
  ApiResponse,
  BoundaryDataType,
  BoundaryInput,
  BoundaryLocation,
  BoundaryTestCase,
} from "../../types/api";
import type { RequestConfiguration } from "../history/history";
import { boundaryInputsFromForm, makeBoundaryRequest } from "./boundaryCases";

interface BoundaryResult {
  testCase: BoundaryTestCase;
  request: ApiRequest | null;
  response: ApiResponse | null;
  error: string | null;
  outcome: "passed" | "failed" | "errored";
}

const EMPTY_INPUT: Omit<BoundaryInput, "name"> = {
  location: "body",
  data_type: "string",
};

function newInput(): BoundaryInput {
  return { name: "", ...EMPTY_INPUT };
}

function outcomeFor(response: ApiResponse): BoundaryResult["outcome"] {
  if (response.error || response.status_code === null) return "errored";
  return response.status_code < 400 ? "passed" : "failed";
}

function describeError(error: unknown): string {
  return error instanceof ApiClientError
    ? error.message
    : error instanceof Error
      ? error.message
      : "This test case could not be executed.";
}

function requestPreview(request: ApiRequest): string {
  return JSON.stringify(request, null, 2);
}

function responsePreview(result: BoundaryResult): string {
  if (result.error) return result.error;
  if (result.response?.error) {
    return `${result.response.error.code}: ${result.response.error.message}`;
  }
  return JSON.stringify(result.response, null, 2) ?? "";
}

export function BoundaryTester({
  configuration,
  onGeneratedCases,
}: {
  configuration: RequestConfiguration;
  onGeneratedCases?: (cases: Array<Record<string, unknown>>) => void;
}) {
  const [inputs, setInputs] = useState<BoundaryInput[]>([newInput()]);
  const [cases, setCases] = useState<BoundaryTestCase[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<BoundaryResult[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [message, setMessage] = useState("");

  const updateInput = (index: number, patch: Partial<BoundaryInput>) => {
    setInputs((previous) =>
      previous.map((input, inputIndex) =>
        inputIndex === index ? { ...input, ...patch } : input,
      ),
    );
  };

  const generate = async () => {
    const validInputs = boundaryInputsFromForm(inputs).filter((input) => input.name);
    if (!validInputs.length) {
      setMessage("Add at least one named input to generate cases.");
      return;
    }
    setIsGenerating(true);
    setMessage("");
    setResults([]);
    try {
      const generated = await generateBoundaryCases(validInputs);
      setCases(generated);
      onGeneratedCases?.(generated.map((testCase) => ({ ...testCase })));
      setSelectedIds(new Set(generated.map((testCase) => testCase.id)));
      setMessage(`${generated.length} generated cases ready for review.`);
    } catch (error) {
      setCases([]);
      setSelectedIds(new Set());
      setMessage(describeError(error));
    } finally {
      setIsGenerating(false);
    }
  };

  const runSelected = async () => {
    const selected = cases.filter((testCase) => selectedIds.has(testCase.id));
    if (!selected.length) {
      setMessage("Select at least one case to run.");
      return;
    }
    if (
      !["GET", "HEAD", "OPTIONS"].includes(configuration.method) &&
      !window.confirm(
        `${configuration.method} may modify or delete data. Run ${selected.length} selected boundary test(s) against the configured target?`,
      )
    ) {
      setMessage("Run cancelled. No requests were sent.");
      return;
    }

    setIsRunning(true);
    setResults([]);
    setMessage("");
    const completed: BoundaryResult[] = [];
    for (const testCase of selected) {
      let request: ApiRequest | null = null;
      try {
        request = makeBoundaryRequest(configuration, testCase);
        const response = await executeRequest(request, { testCase: testCase.label });
        completed.push({
          testCase,
          request,
          response,
          error: null,
          outcome: outcomeFor(response),
        });
      } catch (error) {
        completed.push({
          testCase,
          request,
          response: null,
          error: describeError(error),
          outcome: "errored",
        });
      }
      setResults([...completed]);
    }
    setIsRunning(false);
    setMessage("Test run complete.");
  };

  const toggleCase = (id: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const counts = results.reduce(
    (summary, result) => ({ ...summary, [result.outcome]: summary[result.outcome] + 1 }),
    { passed: 0, failed: 0, errored: 0 },
  );

  return (
    <section
      aria-labelledby="boundary-heading"
      className="mt-8 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80"
    >
      <div className="border-b border-slate-800 p-5 sm:flex sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-cyan-300">
            <FlaskConical size={17} />
            <h2 className="text-sm font-semibold text-slate-100" id="boundary-heading">
              Boundary testing
            </h2>
          </div>
          <p className="mt-2 max-w-2xl text-xs leading-5 text-slate-500">
            Define input fields, review generated edge cases, select which to run, then inspect each result.
            HTTP 2xx/3xx passes; HTTP 4xx/5xx fails; transport or local validation errors are errored.
          </p>
        </div>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-[10px] text-amber-200/80 sm:mt-0">
          <AlertTriangle size={13} />
          {["GET", "HEAD", "OPTIONS"].includes(configuration.method)
            ? "Safe method"
            : `${configuration.method} requires confirmation`}
        </div>
      </div>

      <div className="space-y-3 p-4">
        {inputs.map((input, index) => (
          <div
            className="grid gap-2 rounded-xl border border-slate-800 bg-slate-950/50 p-3 sm:grid-cols-2 lg:grid-cols-[minmax(120px,1fr)_minmax(120px,0.8fr)_minmax(120px,0.8fr)_100px_100px_38px]"
            key={`boundary-input-${index}`}
          >
            <label className="text-[10px] text-slate-500">
              Input name
              <input
                aria-label={`Input name ${index + 1}`}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-400"
                onChange={(event) => updateInput(index, { name: event.target.value })}
                placeholder={input.location === "path" ? "id" : "profile.email"}
                value={input.name}
              />
            </label>
            <label className="text-[10px] text-slate-500">
              Location
              <select
                aria-label={`Input location ${index + 1}`}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                onChange={(event) => {
                  const location = event.target.value as BoundaryLocation;
                  updateInput(index, {
                    location,
                    ...(location !== "body" && ["array", "object"].includes(input.data_type)
                      ? { data_type: "string" }
                      : {}),
                  });
                }}
                value={input.location}
              >
                <option value="path">URL path parameter</option>
                <option value="query">Query parameter</option>
                <option value="header">Header</option>
                <option value="body">JSON body field</option>
              </select>
            </label>
            <label className="text-[10px] text-slate-500">
              Data type
              <select
                aria-label={`Data type ${index + 1}`}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                onChange={(event) =>
                  updateInput(index, {
                    data_type: event.target.value as BoundaryDataType,
                  })
                }
                value={input.data_type}
              >
                <option value="string">String</option>
                <option value="number">Number</option>
                {input.location === "body" && (
                  <>
                    <option value="array">Array</option>
                    <option value="object">Object</option>
                  </>
                )}
              </select>
            </label>
            <label className="text-[10px] text-slate-500">
              Minimum
              <input
                aria-label={`Minimum ${index + 1}`}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-400 disabled:opacity-40"
                disabled={input.data_type !== "number"}
                onChange={(event) =>
                  updateInput(index, {
                    minimum: event.target.value === "" ? undefined : Number(event.target.value),
                  })
                }
                type="number"
                value={input.minimum ?? ""}
              />
            </label>
            <label className="text-[10px] text-slate-500">
              Maximum
              <input
                aria-label={`Maximum ${index + 1}`}
                className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200 outline-none focus:border-cyan-400 disabled:opacity-40"
                disabled={input.data_type !== "number"}
                onChange={(event) =>
                  updateInput(index, {
                    maximum: event.target.value === "" ? undefined : Number(event.target.value),
                  })
                }
                type="number"
                value={input.maximum ?? ""}
              />
            </label>
            <button
              aria-label={`Remove input ${index + 1}`}
              className="self-end rounded-lg p-2 text-slate-500 hover:bg-rose-400/10 hover:text-rose-300"
              onClick={() => setInputs((previous) => previous.filter((_, i) => i !== index))}
              type="button"
            >
              <Trash2 size={15} />
            </button>
          </div>
        ))}
        <div className="flex flex-wrap items-center gap-2">
          <button
            className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:border-slate-500"
            onClick={() => setInputs((previous) => [...previous, newInput()])}
            type="button"
          >
            <Plus size={14} />
            Add input
          </button>
          <button
            className="inline-flex items-center gap-2 rounded-lg bg-cyan-400 px-3 py-2 text-xs font-bold text-slate-950 hover:bg-cyan-300 disabled:opacity-60"
            disabled={isGenerating || isRunning}
            onClick={() => void generate()}
            type="button"
          >
            {isGenerating ? <LoaderCircle className="animate-spin" size={14} /> : <FlaskConical size={14} />}
            Generate cases
          </button>
          {message && <p aria-live="polite" className="text-xs text-slate-400">{message}</p>}
        </div>
      </div>

      {cases.length > 0 && (
        <div className="border-t border-slate-800">
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
            <div className="text-xs font-semibold text-slate-200">
              Generated cases <span className="text-slate-500">({cases.length})</span>
            </div>
            <div className="flex items-center gap-3">
              <button
                className="text-[11px] text-cyan-300 hover:text-cyan-200"
                onClick={() =>
                  setSelectedIds(
                    selectedIds.size === cases.length
                      ? new Set()
                      : new Set(cases.map((testCase) => testCase.id)),
                  )
                }
                type="button"
              >
                {selectedIds.size === cases.length ? "Select none" : "Select all"}
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-lg bg-emerald-400 px-3 py-2 text-xs font-bold text-slate-950 hover:bg-emerald-300 disabled:opacity-50"
                disabled={isRunning || !selectedIds.size}
                onClick={() => void runSelected()}
                type="button"
              >
                {isRunning ? <LoaderCircle className="animate-spin" size={14} /> : <Play size={13} fill="currentColor" />}
                Run selected ({selectedIds.size})
              </button>
            </div>
          </div>
          <ul className="divide-y divide-slate-800/80">
            {cases.map((testCase) => (
              <li className="flex items-start gap-3 px-5 py-3" key={testCase.id}>
                <input
                  aria-label={`Select ${testCase.input_name}: ${testCase.label}`}
                  checked={selectedIds.has(testCase.id)}
                  className="mt-1 size-4 accent-cyan-400"
                  disabled={isRunning}
                  onChange={() => toggleCase(testCase.id)}
                  type="checkbox"
                />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-slate-200">
                    {testCase.input_name} · {testCase.label}
                  </p>
                  <p className="mt-1 text-[10px] text-slate-500">
                    {testCase.location} · {testCase.data_type} ·{" "}
                    <code className="break-all text-cyan-200/70">
                      {testCase.operation === "remove"
                        ? "remove field"
                        : JSON.stringify(testCase.value).slice(0, 120)}
                    </code>
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {results.length > 0 && (
        <div className="border-t border-slate-800">
          <div aria-label="Boundary test summary" className="flex flex-wrap gap-4 px-5 py-3 text-xs">
            <span className="text-emerald-300">Passed {counts.passed}</span>
            <span className="text-rose-300">Failed {counts.failed}</span>
            <span className="text-amber-200">Errored {counts.errored}</span>
          </div>
          <ul className="space-y-3 p-4 pt-0">
            {results.map((result) => (
              <li
                aria-label={`${result.testCase.label} ${result.outcome}`}
                className="rounded-xl border border-slate-800 bg-slate-950/60 p-4"
                key={result.testCase.id}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                    {result.outcome === "passed" ? (
                      <CheckCircle2 className="text-emerald-300" size={15} />
                    ) : (
                      <Circle className={result.outcome === "failed" ? "text-rose-300" : "text-amber-200"} size={15} />
                    )}
                    {result.testCase.input_name} · {result.testCase.label}
                  </p>
                  <p className="text-[11px] text-slate-400">
                    {result.response?.status_code ?? "No status"}
                    {result.response?.execution_time_ms != null
                      ? ` · ${result.response.execution_time_ms.toFixed(1)} ms`
                      : ""}
                    {" · "}
                    <span className="uppercase">{result.outcome}</span>
                  </p>
                </div>
                <details className="mt-3">
                  <summary className="cursor-pointer text-[11px] text-cyan-300">Request and response details</summary>
                  <div className="mt-2 grid gap-2 lg:grid-cols-2">
                    <pre className="max-h-48 overflow-auto rounded-lg bg-slate-900 p-3 text-[10px] leading-5 text-slate-300">
                      <code>{result.request ? requestPreview(result.request) : "Request not built"}</code>
                    </pre>
                    <pre className="max-h-48 overflow-auto rounded-lg bg-slate-900 p-3 text-[10px] leading-5 text-slate-300">
                      <code>{responsePreview(result)}</code>
                    </pre>
                  </div>
                </details>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
