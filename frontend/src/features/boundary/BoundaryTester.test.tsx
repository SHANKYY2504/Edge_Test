import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RequestConfiguration } from "../history/history";
import { BoundaryTester } from "./BoundaryTester";

const configuration: RequestConfiguration = {
  method: "GET",
  url: "http://127.0.0.1:8001/users",
  headers: [],
  queryParams: [],
  body: "",
  timeout: 5,
};

const generatedCases = [
  {
    id: "case-1",
    label: "empty string",
    input_name: "name",
    location: "body",
    data_type: "string",
    operation: "set",
    value: "",
  },
  {
    id: "case-2",
    label: "invalid number type",
    input_name: "name",
    location: "body",
    data_type: "string",
    operation: "set",
    value: 42,
  },
];

function apiResponse(statusCode = 200) {
  return {
    status_code: statusCode,
    headers: {},
    body: { received: true },
    content_type: "application/json",
    is_json: true,
    execution_time_ms: 4.5,
    error: null,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("BoundaryTester", () => {
  it("previews generated cases, runs only the selected case, and summarizes outcomes", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ cases: generatedCases }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(apiResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(<BoundaryTester configuration={configuration} />);

    await user.type(screen.getByRole("textbox", { name: "Input name 1" }), "name");
    await user.click(screen.getByRole("button", { name: "Generate cases" }));

    expect(await screen.findByText("name · empty string")).toBeInTheDocument();
    expect(screen.getByText("name · invalid number type")).toBeInTheDocument();
    await user.click(
      screen.getByRole("checkbox", { name: "Select name: invalid number type" }),
    );
    await user.click(screen.getByRole("button", { name: "Run selected (1)" }));

    expect(await screen.findByLabelText("empty string passed")).toBeInTheDocument();
    expect(screen.getByLabelText("Boundary test summary")).toHaveTextContent(
      "Passed 1",
    );
    expect(screen.getByLabelText("Boundary test summary")).toHaveTextContent(
      "Failed 0",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const executedRequest = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(executedRequest.body).toEqual({ name: "" });
  });

  it("requires confirmation and sends no destructive requests when cancelled", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ cases: generatedCases }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.fn().mockReturnValue(false);
    vi.stubGlobal("confirm", confirmMock);
    render(
      <BoundaryTester
        configuration={{ ...configuration, method: "POST" }}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "Input name 1" }), "name");
    await user.click(screen.getByRole("button", { name: "Generate cases" }));
    await screen.findByText("name · empty string");
    await user.click(screen.getByRole("button", { name: "Run selected (2)" }));

    expect(confirmMock).toHaveBeenCalledOnce();
    expect(await screen.findByText("Run cancelled. No requests were sent.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("marks HTTP error responses failed and transport errors errored", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ cases: generatedCases }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(apiResponse(422)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockRejectedValueOnce(new TypeError("fetch failed"));
    vi.stubGlobal("fetch", fetchMock);
    render(<BoundaryTester configuration={configuration} />);

    await user.type(screen.getByRole("textbox", { name: "Input name 1" }), "name");
    await user.click(screen.getByRole("button", { name: "Generate cases" }));
    await screen.findByText("name · empty string");
    await user.click(screen.getByRole("button", { name: "Run selected (2)" }));

    await waitFor(() => {
      expect(screen.getByLabelText("Boundary test summary")).toHaveTextContent("Failed 1");
      expect(screen.getByLabelText("Boundary test summary")).toHaveTextContent("Errored 1");
    });
    expect(screen.getByLabelText("empty string failed")).toBeInTheDocument();
    expect(screen.getByLabelText("invalid number type errored")).toBeInTheDocument();
  });
});
