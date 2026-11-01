import { describe, expect, it } from "vitest";

import type { RequestConfiguration } from "../history/history";
import type { BoundaryTestCase } from "../../types/api";
import { makeBoundaryRequest } from "./boundaryCases";

const configuration: RequestConfiguration = {
  method: "POST",
  url: "http://127.0.0.1:8001/users/{id}?existing=1",
  headers: [{ id: "h1", key: "X-Test", value: "base", enabled: true }],
  queryParams: [{ id: "q1", key: "page", value: "1", enabled: true }],
  body: '{"profile":{"email":"old@example.test"},"active":true}',
  timeout: 5,
};

function testCase(
  overrides: Partial<BoundaryTestCase> = {},
): BoundaryTestCase {
  return {
    id: "case-1",
    label: "sample",
    input_name: "id",
    location: "path",
    data_type: "string",
    operation: "set",
    value: "Ada Lovelace",
    ...overrides,
  };
}

describe("makeBoundaryRequest", () => {
  it("encodes path values while preserving query strings", () => {
    const request = makeBoundaryRequest(
      configuration,
      testCase({ path_values: { id: 7 } }),
    );

    expect(request.url).toBe(
      "http://127.0.0.1:8001/users/Ada%20Lovelace?existing=1",
    );
    expect(request.query_params).toEqual({ page: "1" });
  });

  it("substitutes all representative path values in the valid baseline case", () => {
    const request = makeBoundaryRequest(
      {
        ...configuration,
        url: "http://127.0.0.1:8001/orgs/{orgId}/users/{id}?existing=1",
      },
      testCase({
        location: "query",
        operation: "remove",
        input_name: "__no_inputs__",
        path_values: { orgId: "team 1", id: 7 },
        value: null,
      }),
    );

    expect(request.url).toBe(
      "http://127.0.0.1:8001/orgs/team%201/users/7?existing=1",
    );
  });

  it("lets a path-boundary value override its representative baseline", () => {
    const request = makeBoundaryRequest(
      configuration,
      testCase({ path_values: { id: 7 }, value: "boundary value" }),
    );

    expect(request.url).toBe(
      "http://127.0.0.1:8001/users/boundary%20value?existing=1",
    );
  });

  it("sets a query value and replaces headers case-insensitively", () => {
    const queryRequest = makeBoundaryRequest(
      configuration,
      testCase({ location: "query", input_name: "page", value: 0 }),
    );
    const headerRequest = makeBoundaryRequest(
      {
        ...configuration,
        headers: [
          ...configuration.headers,
          { id: "h2", key: "x-test", value: "duplicate", enabled: true },
        ],
      },
      testCase({ location: "header", input_name: "x-test", value: "edge" }),
    );

    expect(queryRequest.query_params).toEqual({ page: "0" });
    expect(headerRequest.headers).toEqual({ "x-test": "edge" });
  });

  it("sets and removes nested body fields without changing other values", () => {
    const setRequest = makeBoundaryRequest(
      configuration,
      testCase({
        location: "body",
        input_name: "profile.email",
        value: null,
      }),
    );
    const removeRequest = makeBoundaryRequest(
      configuration,
      testCase({
        location: "body",
        input_name: "profile.email",
        operation: "remove",
        value: null,
      }),
    );

    expect(setRequest.body).toEqual({
      profile: { email: null },
      active: true,
    });
    expect(removeRequest.body).toEqual({ profile: {}, active: true });
  });

  it("reports missing URL path placeholders as local case errors", () => {
    expect(() =>
      makeBoundaryRequest(
        { ...configuration, url: "http://127.0.0.1:8001/users" },
        testCase(),
      ),
    ).toThrow("URL must include the {id} path placeholder.");
  });
});
