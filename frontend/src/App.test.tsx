import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { ApiResponse } from "./types/api";

function response(
  overrides: Partial<ApiResponse> = {},
): ApiResponse {
  return {
    status_code: 200,
    headers: { "content-type": "application/json" },
    body: { ok: true },
    content_type: "application/json",
    is_json: true,
    execution_time_ms: 12.5,
    error: null,
    ...overrides,
  };
}

function mockBackend(payload: unknown, status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    void _init;
    const url = String(input);
    let responsePayload = payload;
    if (url.endsWith("/api/v1/analytics")) {
      responsePayload = {
        total_requests: 0,
        total_test_cases: 0,
        saved_test_case_definitions: 0,
        passed_tests: 0,
        failed_tests: 0,
        errored_tests: 0,
        recent_failures: [],
        failing_endpoints: [],
        trends: [],
      };
    } else if (
      url.endsWith("/api/v1/collections") ||
      url.includes("/api/v1/failures")
    ) {
      responsePayload = [];
    }
    return Promise.resolve(
      new Response(JSON.stringify(responsePayload), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("EdgeTest request workspace", () => {
  it("sends a request using the backend schema and displays a JSON response", async () => {
    const user = userEvent.setup();
    const fetchMock = mockBackend(response());
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("200 OK")).toBeInTheDocument();
    expect(screen.getByText(/"ok": true/)).toBeInTheDocument();
    const requestCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/requests/execute"),
    );
    const request = JSON.parse(requestCall?.[1]?.body as string);
    expect(request).toMatchObject({
      method: "GET",
      url: "http://127.0.0.1:8001/users",
      headers: { Accept: "application/json" },
      query_params: {},
      timeout: 5,
    });
  });

  it("keeps upstream HTTP errors as responses rather than network failures", async () => {
    const user = userEvent.setup();
    mockBackend(
      response({
        status_code: 404,
        body: { detail: "Demo not found" },
        is_json: true,
        execution_time_ms: 3,
      }),
    );
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("404 Not Found")).toBeInTheDocument();
    expect(screen.getByText(/Demo not found/)).toBeInTheDocument();
    expect(screen.queryByText("Network error")).not.toBeInTheDocument();
  });

  it("shows a clear network failure state", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Network error")).toBeInTheDocument();
    expect(
      screen.getAllByText(/Check that it is running/)[0],
    ).toBeInTheDocument();
  });

  it("shows backend transport failures as network errors", async () => {
    const user = userEvent.setup();
    mockBackend(
      response({
        status_code: null,
        error: {
          code: "connection_error",
          message: "A connection to the destination could not be established.",
        },
        body: null,
        execution_time_ms: 2,
      }),
    );
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Network error")).toBeInTheDocument();
    expect(
      screen.getByText(/connection to the destination could not be established/),
    ).toBeInTheDocument();
  });

  it("shows backend request timeout errors", async () => {
    const user = userEvent.setup();
    mockBackend(
      response({
        status_code: null,
        error: { code: "timeout", message: "The request timed out." },
        body: null,
        execution_time_ms: 100,
      }),
    );
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Timed out")).toBeInTheDocument();
    expect(screen.getByText("The request timed out.")).toBeInTheDocument();
  });

  it("shows backend validation errors", async () => {
    const user = userEvent.setup();
    mockBackend(
      { error: { code: "validation_error", message: "The request contains invalid data." } },
      422,
    );
    render(<App />);
    await user.clear(screen.getByRole("textbox", { name: "Request URL" }));
    await user.type(screen.getByRole("textbox", { name: "Request URL" }), "bad url");

    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/request contains invalid data/)).toBeInTheDocument();
  });

  it("validates JSON locally before sending", async () => {
    const user = userEvent.setup();
    const fetchMock = mockBackend(response());
    render(<App />);

    await user.click(
      within(screen.getByRole("tablist", { name: "Request options" })).getByRole(
        "tab",
        { name: "Body" },
      ),
    );
    const body = screen.getByRole("textbox", { name: "JSON request body" });
    fireEvent.change(body, { target: { value: "{invalid" } });
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(screen.getByText("Enter valid JSON before sending.")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("/api/v1/requests/execute"),
      ),
    ).toBe(false);

    await user.clear(body);
    fireEvent.change(body, { target: { value: '{"name":"Ada"}' } });
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) =>
          String(url).includes("/api/v1/requests/execute"),
        ),
      ).toHaveLength(1),
    );
    const requestCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/requests/execute"),
    );
    expect(JSON.parse(requestCall?.[1]?.body as string).body).toEqual({
      name: "Ada",
    });
  });

  it("adds and removes header and query parameter rows", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Add row" }));
    expect(screen.getByRole("textbox", { name: "Headers key 2" })).toBeInTheDocument();
    await user.click(
      within(screen.getByRole("tablist", { name: "Request options" })).getByRole(
        "tab",
        { name: "Query Params" },
      ),
    );
    await user.click(screen.getByRole("button", { name: "Add parameter" }));
    expect(
      screen.getByRole("textbox", { name: "Query Params key 1" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete query params row 1" }));
    expect(
      screen.queryByRole("textbox", { name: "Query Params key 1" }),
    ).not.toBeInTheDocument();
  });

  it("records session history and restores a redacted configuration without sending", async () => {
    const user = userEvent.setup();
    const fetchMock = mockBackend(response());
    render(<App />);

    await user.clear(screen.getByRole("textbox", { name: "Request URL" }));
    await user.type(
      screen.getByRole("textbox", { name: "Request URL" }),
      "https://person:password@example.test?access_token=secret",
    );
    await user.click(screen.getByRole("button", { name: "Add row" }));
    await user.type(screen.getByRole("textbox", { name: "Headers key 2" }), "Authorization");
    await user.type(screen.getByRole("textbox", { name: "Headers value 2" }), "Bearer super-secret");
    await user.click(
      within(screen.getByRole("tablist", { name: "Request options" })).getByRole(
        "tab",
        { name: "Body" },
      ),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "JSON request body" }), {
      target: { value: '{"api_key":"body-secret","note":"kept"}' },
    });

    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(screen.getAllByText(/access_token=%5BREDACTED%5D/).length).toBeGreaterThan(0),
    );
    const restoreButton = screen
      .getAllByRole("button")
      .find((button) => button.textContent?.includes("REDACTED"));
    expect(restoreButton).toBeDefined();
    if (!restoreButton) throw new Error("Redacted history item was not rendered.");
    expect(restoreButton).toBeInTheDocument();
    expect(screen.queryByText("super-secret")).not.toBeInTheDocument();
    await user.clear(screen.getByRole("textbox", { name: "Request URL" }));
    await user.click(restoreButton);
    expect(screen.getByRole("textbox", { name: "Request URL" })).toHaveValue(
      "https://example.test/?access_token=%5BREDACTED%5D",
    );
    expect(screen.getByRole("textbox", { name: "Headers value 2" })).toHaveValue(
      "[REDACTED]",
    );
    await user.click(
      within(screen.getByRole("tablist", { name: "Request options" })).getByRole(
        "tab",
        { name: "Body" },
      ),
    );
    expect(screen.getByRole("textbox", { name: "JSON request body" })).toHaveValue(
      '{\n  "api_key": "[REDACTED]",\n  "note": "kept"\n}',
    );
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).includes("/api/v1/requests/execute"),
      ),
    ).toHaveLength(1);
    expect(screen.getByText("Response will appear here")).toBeInTheDocument();
  });

  it("clears request history", async () => {
    const user = userEvent.setup();
    mockBackend(response());
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Send" }));
    const historyList = screen.getAllByRole("list")[0];
    expect(within(historyList).getAllByRole("listitem")).toHaveLength(1);
    await user.click(
      screen.getAllByRole("button", { name: "Clear request history" })[0],
    );
    expect(screen.getByText("No requests yet")).toBeInTheDocument();
  });
});
