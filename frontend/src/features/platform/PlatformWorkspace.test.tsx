import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RequestConfiguration } from "../history/history";
import { PlatformWorkspace } from "./PlatformWorkspace";

const configuration: RequestConfiguration = {
  method: "GET",
  url: "http://127.0.0.1:8001/users",
  headers: [],
  queryParams: [],
  body: "",
  timeout: 5,
};

const failure = {
  id: "failure-1",
  collection_id: null,
  saved_test_id: null,
  replay_of: null,
  method: "GET",
  endpoint: "http://127.0.0.1:8001/errors/500",
  status_code: 500,
  outcome: "failed",
  error_category: "http_error",
  duration_ms: 10,
  test_case: null,
  request: {
    method: "GET",
    url: "http://127.0.0.1:8001/errors/500",
    headers: {},
    query_params: {},
    timeout: 5,
  },
  response: {
    status_code: 500,
    headers: {},
    body: { message: "Expected sanitized error" },
    content_type: "application/json",
    is_json: true,
    execution_time_ms: 10,
    error: null,
  },
  created_at: "2026-10-31T12:00:00+00:00",
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PlatformWorkspace", () => {
  it("shows stored analytics and lets users replay and analyze failures", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/analytics")) {
        return jsonResponse({
          total_requests: 9,
          total_test_cases: 3,
          saved_test_case_definitions: 4,
          passed_tests: 6,
          failed_tests: 2,
          errored_tests: 1,
          recent_failures: [failure],
          failing_endpoints: [{ method: "GET", endpoint: failure.endpoint, failures: 2 }],
          trends: [{ date: "2026-10-31", total: 9, passed: 6, failed: 3 }],
        });
      }
      if (url.endsWith("/collections")) return jsonResponse([]);
      if (url.endsWith("/failures")) return jsonResponse([failure]);
      if (url.endsWith("/replay")) {
        return jsonResponse({ original: failure, replay: { ...failure, id: "replay-1", status_code: 200, outcome: "passed" } });
      }
      if (url.endsWith("/analyze")) {
        expect(JSON.parse(String(init?.body))).toEqual({ consent: true });
        return jsonResponse({
          error_summary: "The service reported an internal error.",
          possible_root_cause: "The service reported an internal error.",
          explanation: "The test received a server error.",
          debugging_steps: ["Inspect service logs."],
          potential_issues: ["An unhandled exception."],
          suggested_corrections: ["Add error handling."],
          provider: "configured LLM",
          model: "test-model",
        });
      }
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));

    render(
      <PlatformWorkspace
        configuration={configuration}
        generatedTestCases={[]}
        onRestoreRequest={vi.fn()}
      />,
    );

    expect(await screen.findByText("Tests, failures & analytics")).toBeInTheDocument();
    expect(screen.getAllByText("9").length).toBeGreaterThan(0);
    await user.click(await screen.findByRole("button", { name: "Replay" }));
    expect(await screen.findByLabelText("Replay comparison")).toBeInTheDocument();
    expect(screen.getByText(/"status": 200/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "AI analysis" }));
    expect(await screen.findByLabelText("AI error summary")).toHaveTextContent(
      "The service reported an internal error.",
    );
    expect(screen.getByText("AI recommendations · not a guaranteed diagnosis")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/executions/failure-1/analyze"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates and selects a collection then saves the current request", async () => {
    const user = userEvent.setup();
    const collection = {
      id: "collection-1",
      name: "Smoke",
      created_at: "2026-10-31T12:00:00+00:00",
      saved_test_count: 0,
    };
    const calls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/analytics")) return jsonResponse({
        total_requests: 0, total_test_cases: 0, passed_tests: 0, failed_tests: 0,
        saved_test_case_definitions: 0,
        errored_tests: 0, recent_failures: [], failing_endpoints: [], trends: [],
      });
      if (url.endsWith("/collections") && init?.method === "POST") return jsonResponse(collection, 201);
      if (url.endsWith("/collections")) return jsonResponse([collection]);
      if (url.endsWith("/failures")) return jsonResponse([]);
      if (url.endsWith("/tests") && init?.method === "POST") return jsonResponse({
        id: "saved-1", collection_id: collection.id, name: "GET demo",
        request: { ...configuration, headers: {}, query_params: {} }, test_cases: [],
        created_at: "2026-10-31T12:00:00+00:00", updated_at: "2026-10-31T12:00:00+00:00",
      }, 201);
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("prompt", vi.fn().mockReturnValue("Smoke"));

    render(
      <PlatformWorkspace
        configuration={configuration}
        generatedTestCases={[{ id: "boundary-case", label: "empty value" }]}
        onRestoreRequest={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "New collection" }));
    await screen.findByRole("option", { name: "Smoke" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Select test collection" }), "collection-1");
    await user.click(await screen.findByRole("button", { name: "Save current request" }));
    expect(await screen.findByText(/Saved request and test cases/)).toBeInTheDocument();
    expect(calls.some((url) => url.endsWith("/collections/collection-1/tests"))).toBe(true);
    const savedPayload = fetchMock.mock.calls.find(
      ([url, init]) => String(url).endsWith("/collections/collection-1/tests") && init?.method === "POST",
    );
    expect(JSON.parse(String(savedPayload?.[1]?.body)).test_cases).toEqual([
      { id: "boundary-case", label: "empty value" },
    ]);
  });
});
