import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OpenApiTester } from "./OpenApiTester";

const importedDocument = {
  title: "Test API",
  version: "1",
  specification_version: "3.1.0",
  servers: ["http://127.0.0.1:8001"],
  warnings: [],
  schemas: [],
  operations: [
    {
      id: "getUser",
      method: "GET",
      path: "/users/{id}",
      summary: "Get a user",
      tags: ["users"],
      request: {
        url: "http://127.0.0.1:8001/users/%7Bid%7D",
        method: "GET",
        headers: {},
        query_params: {},
        body: null,
        timeout: 10,
      },
      parameters: [],
      request_body_schema: null,
      warnings: [],
      cases: [
        {
          id: "valid-request",
          label: "valid representative request",
          input_name: "__no_inputs__",
          location: "query",
          data_type: "string",
          operation: "remove",
          value: null,
          path_values: { id: 7 },
        },
        {
          id: "missing-id",
          label: "missing parameter: id",
          input_name: "id",
          location: "path",
          data_type: "number",
          operation: "remove",
          value: null,
        },
      ],
    },
  ],
};

function importResponse(payload: unknown = importedDocument, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function executeResponse(statusCode = 200) {
  return new Response(
    JSON.stringify({
      status_code: statusCode,
      headers: { "content-type": "application/json" },
      body: { ok: true },
      content_type: "application/json",
      is_json: true,
      execution_time_ms: 8.25,
      error: null,
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("OpenApiTester", () => {
  it("imports a file and displays discovered endpoints and generated cases", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(importResponse());
    vi.stubGlobal("fetch", fetchMock);
    render(<OpenApiTester />);
    const file = new File(
      ['{"openapi":"3.1.0"}'],
      "api.json",
      { type: "application/json" },
    );
    await user.upload(
      screen.getByLabelText("Upload OpenAPI specification"),
      file,
    );

    expect(await screen.findByText("Test API")).toBeInTheDocument();
    expect(screen.getByText("/users/{id}")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      specification: '{"openapi":"3.1.0"}',
    });

    await user.click(
      screen.getByRole("checkbox", { name: "Select endpoint GET /users/{id}" }),
    );
    expect(
      await screen.findByText("valid representative request"),
    ).toBeInTheDocument();
    expect(screen.getByText("missing parameter: id")).toBeInTheDocument();
  });

  it("submits a supplied base URL when the specification has no servers", async () => {
    const user = userEvent.setup();
    const noServers = {
      ...importedDocument,
      servers: [],
      operations: [{
        ...importedDocument.operations[0],
        request: {
          ...importedDocument.operations[0].request,
          url: "https://api.example.test/v1/users/%7Bid%7D",
        },
      }],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(importResponse(
        { error: { code: "http_error", message: "No server URL is defined for this operation. Provide a Base URL to import this specification." } },
        422,
      ))
      .mockResolvedValueOnce(importResponse({
        ...noServers,
        servers: ["https://api.example.test/v1"],
      }));
    vi.stubGlobal("fetch", fetchMock);
    render(<OpenApiTester />);
    const file = new File(['{"openapi":"3.1.0"}'], "api.json", {
      type: "application/json",
    });
    const upload = screen.getByLabelText("Upload OpenAPI specification");
    await user.upload(upload, file);
    expect(await screen.findByText(/Provide a Base URL/)).toBeInTheDocument();

    await user.type(
      screen.getByRole("textbox", { name: "OpenAPI base URL" }),
      "https://api.example.test/v1",
    );
    await user.upload(upload, file);
    expect(await screen.findByText("Test API")).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toMatchObject({
      specification: '{"openapi":"3.1.0"}',
      base_url: "https://api.example.test/v1",
    });
  });

  it("allows choosing among multiple specification servers for execution", async () => {
    const user = userEvent.setup();
    const multipleServers = {
      ...importedDocument,
      operations: [{
        ...importedDocument.operations[0],
        cases: importedDocument.operations[0].cases.slice(0, 1),
      }],
      servers: [
        "http://127.0.0.1:8001",
        "https://secondary.example.test/v2",
      ],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(importResponse(multipleServers))
      .mockResolvedValueOnce(executeResponse());
    vi.stubGlobal("fetch", fetchMock);
    render(<OpenApiTester />);
    await user.type(
      screen.getByRole("textbox", { name: "OpenAPI specification URL" }),
      "https://spec.example.test/openapi.json",
    );
    await user.click(screen.getByRole("button", { name: "Import URL" }));
    await screen.findByText("Test API");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "API server" }),
      "https://secondary.example.test/v2",
    );
    await user.click(
      screen.getByRole("checkbox", {
        name: "Select endpoint GET /users/{id}",
      }),
    );
    await user.click(
      screen.getByRole("button", { name: "Run selected suite" }),
    );

    expect(await screen.findByText("OpenAPI test run complete.")).toBeInTheDocument();
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string).url).toBe(
      "https://secondary.example.test/v2/users/7",
    );
  });

  it("validates a spec URL, sends a selected case, and displays its outcome", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(importResponse())
      .mockResolvedValueOnce(executeResponse());
    vi.stubGlobal("fetch", fetchMock);
    render(<OpenApiTester />);

    await user.type(
      screen.getByRole("textbox", { name: "OpenAPI specification URL" }),
      "https://spec.example.test/openapi.json",
    );
    await user.click(screen.getByRole("button", { name: "Import URL" }));
    await screen.findByText("Test API");
    const endpointCheckbox = screen.getByRole("checkbox", {
      name: "Select endpoint GET /users/{id}",
    });
    await user.click(endpointCheckbox);
    await user.click(screen.getByRole("button", { name: "Toggle all cases" }));
    await user.click(
      screen.getByRole("checkbox", {
        name: "Select case GET /users/{id} valid representative request",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Run selected suite" }));

    expect(await screen.findByText("OpenAPI test run complete.")).toBeInTheDocument();
    expect(screen.getByLabelText("OpenAPI test summary")).toHaveTextContent(
      "Passed 1",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const executedRequest = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(executedRequest).toMatchObject({
      method: "GET",
      url: "http://127.0.0.1:8001/users/7",
    });
    expect(screen.getByText(/8.3 ms/)).toBeInTheDocument();
  });

  it("stops a destructive suite when the confirmation is declined", async () => {
    const user = userEvent.setup();
    const document = {
      ...importedDocument,
      operations: [
        {
          ...importedDocument.operations[0],
          id: "createUser",
          method: "POST",
          path: "/users",
          request: {
            ...importedDocument.operations[0].request,
            method: "POST",
            url: "http://127.0.0.1:8001/users",
          },
        },
      ],
    };
    const fetchMock = vi.fn().mockResolvedValue(importResponse(document));
    vi.stubGlobal("fetch", fetchMock);
    const confirmMock = vi.fn().mockReturnValue(false);
    vi.stubGlobal("confirm", confirmMock);
    render(<OpenApiTester />);

    await user.type(
      screen.getByRole("textbox", { name: "OpenAPI specification URL" }),
      "https://spec.example.test/openapi.json",
    );
    await user.click(screen.getByRole("button", { name: "Import URL" }));
    await screen.findByText("Test API");
    await user.click(
      screen.getByRole("checkbox", { name: "Select endpoint POST /users" }),
    );
    await user.click(screen.getByRole("button", { name: "Run selected suite" }));

    expect(confirmMock).toHaveBeenCalledOnce();
    expect(await screen.findByText("Run cancelled. No requests were sent.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("shows clear parsing errors and rejects credentialed spec URLs before fetching", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn().mockResolvedValue(
      importResponse(
        { error: { code: "http_error", message: "The specification is invalid." } },
        422,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<OpenApiTester />);
    await user.type(
      screen.getByRole("textbox", { name: "OpenAPI specification URL" }),
      "https://user@spec.example.test/openapi.json",
    );
    await user.click(screen.getByRole("button", { name: "Import URL" }));
    expect(await screen.findByText("Specification URLs must not contain credentials.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    await user.clear(screen.getByRole("textbox", { name: "OpenAPI specification URL" }));
    await user.type(
      screen.getByRole("textbox", { name: "OpenAPI specification URL" }),
      "https://spec.example.test/openapi.json",
    );
    await user.click(screen.getByRole("button", { name: "Import URL" }));
    expect(await screen.findByText("The specification is invalid.")).toBeInTheDocument();
  });

  it("rejects oversized or unsupported files before uploading", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<OpenApiTester />);
    const oversized = new File(["x".repeat(1_048_577)], "api.yaml");
    await user.upload(
      screen.getByLabelText("Upload OpenAPI specification"),
      oversized,
    );
    expect(await screen.findByText("Specification files must be 1 MiB or smaller.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
