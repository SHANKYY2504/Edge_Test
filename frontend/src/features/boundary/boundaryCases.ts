import type {
  ApiRequest,
  BoundaryInput,
  BoundaryTestCase,
  JsonValue,
} from "../../types/api";
import type { RequestConfiguration } from "../history/history";

function stringifyValue(value: JsonValue): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function replacePathValue(url: string, name: string, value: JsonValue): string {
  const replacement = encodeURIComponent(stringifyValue(value));
  const placeholder = `{${name}}`;
  const encodedPlaceholder = `%7B${encodeURIComponent(name)}%7D`;
  const escapedEncodedPlaceholder = encodedPlaceholder.replace(
    /[.*+?^${}()|[\]\\]/g,
    "\\$&",
  );
  return url
    .replaceAll(placeholder, replacement)
    .replaceAll(new RegExp(escapedEncodedPlaceholder, "gi"), replacement);
}

function setBodyPath(
  root: Record<string, JsonValue>,
  path: string,
  value: JsonValue,
  remove: boolean,
) {
  const parts = path.split(".");
  let parent = root;
  for (const part of parts.slice(0, -1)) {
    const child = parent[part];
    if (remove && (typeof child !== "object" || child === null || Array.isArray(child))) {
      return;
    }
    if (typeof child !== "object" || child === null || Array.isArray(child)) {
      parent[part] = {};
    }
    parent = parent[part] as Record<string, JsonValue>;
  }
  if (remove) {
    delete parent[parts[parts.length - 1]];
  } else {
    parent[parts[parts.length - 1]] = value;
  }
}

export function makeBoundaryRequest(
  configuration: RequestConfiguration,
  testCase: BoundaryTestCase,
): ApiRequest {
  const request: ApiRequest = {
    method: configuration.method,
    url: configuration.url.trim(),
    headers: Object.fromEntries(
      configuration.headers
        .filter((header) => header.enabled && header.key.trim())
        .map((header) => [header.key.trim(), header.value]),
    ),
    query_params: Object.fromEntries(
      configuration.queryParams
        .filter((parameter) => parameter.enabled && parameter.key.trim())
        .map((parameter) => [parameter.key.trim(), parameter.value]),
    ),
    timeout: configuration.timeout,
  };
  for (const [name, value] of Object.entries(testCase.path_values ?? {})) {
    if (testCase.location === "path" && name === testCase.input_name) continue;
    request.url = replacePathValue(request.url, name, value);
  }

  if (testCase.location === "path") {
    const delimiter = request.url.search(/[?#]/);
    const urlPath = delimiter < 0 ? request.url : request.url.slice(0, delimiter);
    const placeholder = `{${testCase.input_name}}`;
    const encodedPlaceholder = `%7B${encodeURIComponent(testCase.input_name)}%7D`;
    if (!urlPath.includes(placeholder) && !urlPath.toLowerCase().includes(encodedPlaceholder.toLowerCase())) {
      throw new Error(`URL must include the {${testCase.input_name}} path placeholder.`);
    }
    const replacement =
      testCase.operation === "remove"
        ? ""
        : encodeURIComponent(stringifyValue(testCase.value));
    request.url =
      urlPath
        .replaceAll(placeholder, replacement)
        .replaceAll(
          new RegExp(
            encodedPlaceholder.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
            "gi",
          ),
          replacement,
        ) +
      (delimiter < 0 ? "" : request.url.slice(delimiter));
  } else if (testCase.location === "query") {
    if (testCase.operation === "remove") {
      delete request.query_params[testCase.input_name];
    } else {
      request.query_params[testCase.input_name] = stringifyValue(testCase.value);
    }
  } else if (testCase.location === "header") {
    const existing = Object.keys(request.headers).filter(
      (key) => key.toLowerCase() === testCase.input_name.toLowerCase(),
    );
    for (const key of existing) delete request.headers[key];
    if (testCase.operation !== "remove") {
      request.headers[testCase.input_name] = stringifyValue(testCase.value);
    }
  } else {
    if (testCase.input_name === "__root__") {
      if (testCase.operation === "remove") {
        delete request.body;
      } else {
        request.body = testCase.value;
      }
      return request;
    }
    let body: unknown = {};
    if (configuration.body.trim()) {
      body = JSON.parse(configuration.body) as unknown;
    }
    if (typeof body !== "object" || body === null || Array.isArray(body)) {
      throw new Error("Boundary body fields require a JSON object request body.");
    }
    const bodyValue = body as Record<string, JsonValue>;
    setBodyPath(
      bodyValue,
      testCase.input_name,
      testCase.value,
      testCase.operation === "remove",
    );
    request.body = bodyValue;
  }

  return request;
}

export function boundaryInputsFromForm(
  inputs: BoundaryInput[],
): BoundaryInput[] {
  return inputs.map((input) => ({
    ...input,
    name: input.name.trim(),
    ...(input.minimum === undefined ? {} : { minimum: input.minimum }),
    ...(input.maximum === undefined ? {} : { maximum: input.maximum }),
  }));
}
