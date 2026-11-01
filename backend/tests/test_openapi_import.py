import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.openapi_import import OpenApiImportError, import_openapi_text

OPENAPI_JSON = """{
  "openapi": "3.0.3",
  "info": {"title": "Sample API", "version": "2.1"},
  "servers": [{"url": "https://api.example.test/v1"}],
  "paths": {
    "/users/{id}": {
      "parameters": [
        {"name": "id", "in": "path", "required": true, "schema": {"type": "integer", "minimum": 1, "maximum": 99}}
      ],
      "get": {
        "operationId": "getUser",
        "summary": "Get a user",
        "parameters": [
          {"name": "verbose", "in": "query", "schema": {"type": "boolean"}}
        ],
        "responses": {"200": {"description": "OK"}}
      }
    },
    "/users": {
      "post": {
        "operationId": "createUser",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {"$ref": "#/components/schemas/User"}
            }
          }
        },
        "responses": {"201": {"description": "Created"}}
      }
    }
  },
  "components": {
    "schemas": {
      "User": {
        "type": "object",
        "required": ["name", "age"],
        "properties": {
          "name": {"type": "string", "example": "Ada"},
          "age": {"type": "integer", "minimum": 1, "maximum": 120},
          "tags": {"type": "array", "items": {"type": "string"}},
          "profile": {
            "type": "object",
            "properties": {"email": {"type": "string", "format": "email"}}
          }
        }
      }
    }
  }
}"""

OPENAPI_YAML = """openapi: 3.1.0
info:
  title: YAML API
  version: '1'
servers:
  - url: https://yaml.example.test
paths:
  /items:
    get:
      operationId: listItems
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
            minimum: 1
            maximum: 10
      responses:
        '200':
          description: OK
"""


def test_imports_openapi_json_and_generates_cases_from_parameters_and_schema() -> None:
    imported = import_openapi_text(OPENAPI_JSON)
    get_user = next(operation for operation in imported.operations if operation.id == "getUser")
    create_user = next(
        operation for operation in imported.operations if operation.id == "createUser"
    )

    assert imported.title == "Sample API"
    assert imported.version == "2.1"
    assert imported.specification_version == "3.0.3"
    assert str(get_user.request.url) == "https://api.example.test/v1/users/%7Bid%7D"
    assert get_user.request.query_params == {"verbose": "true"}
    valid_get = next(
        case for case in get_user.cases if case.label == "valid representative request"
    )
    assert valid_get.path_values == {"id": 1}
    assert any(case.label == "missing parameter: id" for case in get_user.cases)
    assert any(case.label == "missing parameter: verbose" for case in get_user.cases)
    assert create_user.request.body == {
        "name": "Ada",
        "age": 1,
        "tags": ["boundary"],
        "profile": {"email": "boundary"},
    }
    assert create_user.request_body_schema is not None
    assert [schema.name for schema in imported.schemas] == ["User"]
    assert imported.schemas[0].schema_value["properties"]["name"]["type"] == "string"
    assert any(case.label == "valid representative request" for case in create_user.cases)
    assert any(case.label == "missing field: age" for case in create_user.cases)
    assert any(case.label == "empty object" for case in create_user.cases)
    assert any(case.label == "large array (100 elements)" for case in create_user.cases)
    assert len({case.id for case in create_user.cases}) == len(create_user.cases)
    assert any(case.input_name == "profile" and case.label == "empty object" for case in create_user.cases)


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_openapi_without_servers_requires_and_uses_supplied_base_url(version: str) -> None:
    document = json.dumps(
        {
            "openapi": version,
            "info": {"title": "Serverless API", "version": "1"},
            "paths": {"/v1/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
        }
    )
    with pytest.raises(
        OpenApiImportError,
        match="No server URL is defined.*Provide a Base URL",
    ):
        import_openapi_text(document)

    imported = import_openapi_text(document, "https://api.example.test/root")
    assert imported.servers == ["https://api.example.test/root"]
    assert imported.operations[0].method.value == "GET"
    assert str(imported.operations[0].request.url) == (
        "https://api.example.test/root/v1/items"
    )


def test_import_preserves_multiple_declared_servers_and_uses_the_first_by_default() -> None:
    document = json.loads(OPENAPI_JSON)
    document["servers"] = [
        {"url": "https://primary.example.test/api"},
        {"url": "https://secondary.example.test/v2"},
    ]

    imported = import_openapi_text(
        json.dumps(document),
        "https://user-provided.example.test/ignored",
    )
    operation = next(item for item in imported.operations if item.id == "getUser")

    assert imported.servers == [
        "https://primary.example.test/api",
        "https://secondary.example.test/v2",
    ]
    assert str(operation.request.url) == (
        "https://primary.example.test/api/users/%7Bid%7D"
    )


def test_imports_yaml_and_swagger_2() -> None:
    imported_yaml = import_openapi_text(OPENAPI_YAML)
    assert imported_yaml.title == "YAML API"
    assert imported_yaml.operations[0].method.value == "GET"
    assert len(imported_yaml.operations[0].cases) > 1

    swagger = import_openapi_text(
        """swagger: '2.0'
info:
  title: Legacy API
  version: '1'
host: legacy.example.test
basePath: /v2
schemes: [https]
paths:
  /users:
    post:
      parameters:
        - in: body
          name: user
          required: true
          schema:
            type: object
            properties:
              name:
                type: string
      responses:
        '201':
          description: created
"""
    )
    assert swagger.specification_version == "Swagger 2.0"
    assert swagger.operations[0].request.url.host == "legacy.example.test"
    assert swagger.operations[0].request.body == {"name": "boundary"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json: [", "could not be parsed"),
        ('{"openapi":"2.0","paths":{}}', "Only OpenAPI"),
        ('{"openapi":"3.1.0","paths":{}}', "No supported HTTP operations"),
        (
            """openapi: 3.1.0
info: {title: Aliases, version: '1'}
paths: {}
copy: &shared {type: string}
alias: *shared
""",
            "YAML aliases are not supported",
        ),
    ],
)
def test_invalid_or_unsupported_specifications_fail_gracefully(
    text: str, message: str
) -> None:
    with pytest.raises(OpenApiImportError, match=message):
        import_openapi_text(text)


def test_rejects_external_refs_unsupported_schema_and_server_credentials() -> None:
    external_reference = OPENAPI_JSON.replace(
        "#/components/schemas/User", "https://attacker.example/schema.json"
    )
    unsupported_schema = OPENAPI_JSON.replace(
        '{"type": "string", "example": "Ada"}',
        '{"not": {"type": "number"}}',
    )
    credentials = OPENAPI_JSON.replace(
        "https://api.example.test/v1", "https://user:password@api.example.test/v1"
    )

    credentials_document = json.loads(OPENAPI_JSON)
    credentials_document["servers"][0]["url"] = (
        "https://user@" + "api.example.test/v1"
    )
    credentials = json.dumps(credentials_document)

    for spec, expected in (
        (external_reference, "External schema references are not supported"),
        (unsupported_schema, "not"),
        (credentials, "credentials are not allowed"),
    ):
        with pytest.raises(OpenApiImportError, match=expected):
            import_openapi_text(spec)


def test_generates_payloads_for_nested_compositions_references_and_nullable_fields() -> None:
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Composed schemas", "version": "1"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/records": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": [
                                        "choice",
                                        "mode",
                                        "record",
                                        "nullableField",
                                    ],
                                    "properties": {
                                        "choice": {
                                            "anyOf": [
                                                {"type": "string", "enum": ["alpha"]},
                                                {"type": "integer", "minimum": 5},
                                            ]
                                        },
                                        "mode": {
                                            "oneOf": [
                                                {"type": "string", "enum": ["only"]},
                                                {"type": "integer", "minimum": 3},
                                            ]
                                        },
                                        "record": {"$ref": "#/components/schemas/Record"},
                                        "nullableField": {
                                            "type": "string",
                                            "nullable": True,
                                        },
                                    },
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "anyOf": [
                                            {"$ref": "#/components/schemas/Record"},
                                            {"type": "string"},
                                        ]
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "BaseRecord": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "integer", "minimum": 2},
                        "optionalNote": {"type": "string", "nullable": True},
                    },
                },
                "Record": {
                    "allOf": [
                        {"$ref": "#/components/schemas/BaseRecord"},
                        {
                            "type": "object",
                            "required": ["name"],
                            "properties": {
                                "id": {"type": "integer", "maximum": 3},
                                "name": {"type": "string", "enum": ["Ada"]}
                            },
                        },
                    ]
                },
            }
        },
    }

    imported = import_openapi_text(json.dumps(document))
    operation = imported.operations[0]
    payload = operation.request.body

    assert payload == {
        "choice": "alpha",
        "mode": "only",
        "nullableField": "boundary",
        "record": {"id": 2, "name": "Ada", "optionalNote": "boundary"},
    }
    assert operation.request_body_schema is not None
    assert operation.request_body_schema["properties"]["choice"]["anyOf"]
    assert operation.request_body_schema["properties"]["mode"]["oneOf"]
    assert any(case.label == "missing field: record.name" for case in operation.cases)
    assert len(operation.cases) > 1


def test_reports_specific_error_when_one_of_has_no_unique_representative() -> None:
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Ambiguous oneOf", "version": "1"},
        "servers": [{"url": "https://api.example.test"}],
        "paths": {
            "/records": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {"type": "string"},
                                        {"type": "string"},
                                    ]
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    with pytest.raises(OpenApiImportError, match="oneOf.*exactly one"):
        import_openapi_text(json.dumps(document))


def test_rejects_recursive_schema_references() -> None:
    recursive = """openapi: 3.1.0
info:
  title: Recursive
  version: '1'
servers:
  - url: https://recursive.example.test
paths:
  /nodes:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Node'
      responses:
        '200':
          description: OK
components:
  schemas:
    Node:
      type: object
      properties:
        child:
          $ref: '#/components/schemas/Node'
"""
    with pytest.raises(OpenApiImportError, match="Recursive schema references"):
        import_openapi_text(recursive)


def test_rejects_path_template_without_a_parameter_definition() -> None:
    invalid = """openapi: 3.1.0
info:
  title: Missing path parameter
  version: '1'
servers:
  - url: https://example.test
paths:
  /users/{id}:
    get:
      responses:
        '200':
          description: OK
"""
    with pytest.raises(OpenApiImportError, match="missing parameter definitions"):
        import_openapi_text(invalid)


def test_specification_url_redirect_is_not_followed() -> None:
    requested_urls = []

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            request=request,
            headers={"location": "https://redirect.example.test/openapi.json"},
        )

    async def resolver(host: str, port: int):
        del host, port
        return ("93.184.216.34",)

    application = create_app(
        settings=Settings(outbound_allowed_hosts="spec.example.test"),
        transport=httpx.MockTransport(transport_handler),
        resolver=resolver,
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/openapi/import",
            json={"url": "https://spec.example.test/openapi.json"},
        )

    assert response.status_code == 422
    assert requested_urls == ["https://spec.example.test/openapi.json"]


def test_import_endpoint_accepts_uploaded_specification() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/openapi/import",
            json={"specification": OPENAPI_JSON},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "Sample API"
    assert len(response.json()["operations"]) == 2


def test_import_endpoint_requires_and_accepts_base_url_for_serverless_spec() -> None:
    specification = json.dumps(
        {
            "openapi": "3.1.0",
            "info": {"title": "No servers", "version": "1"},
            "paths": {"/users": {"get": {"responses": {"200": {"description": "OK"}}}}},
        }
    )
    with TestClient(create_app()) as client:
        missing = client.post(
            "/api/v1/openapi/import",
            json={"specification": specification},
        )
        supplied = client.post(
            "/api/v1/openapi/import",
            json={
                "specification": specification,
                "base_url": "https://api.example.test/v1",
            },
        )

    assert missing.status_code == 422
    assert "Provide a Base URL" in missing.json()["error"]["message"]
    assert supplied.status_code == 200
    assert supplied.json()["servers"] == ["https://api.example.test/v1"]
    assert supplied.json()["operations"][0]["request"]["url"] == (
        "https://api.example.test/v1/users"
    )


def test_import_endpoint_fetches_url_through_existing_outbound_policy() -> None:
    async def transport_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://spec.example.test/openapi.json"
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/yaml"},
            text=OPENAPI_YAML,
        )

    async def resolver(host: str, port: int):
        assert host == "spec.example.test"
        assert port == 443
        return ("93.184.216.34",)

    application = create_app(
        settings=Settings(outbound_allowed_hosts="spec.example.test"),
        transport=httpx.MockTransport(transport_handler),
        resolver=resolver,
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/openapi/import",
            json={"url": "https://spec.example.test/openapi.json"},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "YAML API"


def test_import_endpoint_uses_base_url_for_fetched_serverless_specification() -> None:
    serverless_specification = json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Fetched serverless API", "version": "1"},
            "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
        }
    )

    async def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            text=serverless_specification,
        )

    async def resolver(host: str, port: int):
        assert host == "spec.example.test"
        assert port == 443
        return ("93.184.216.34",)

    application = create_app(
        settings=Settings(outbound_allowed_hosts="spec.example.test"),
        transport=httpx.MockTransport(transport_handler),
        resolver=resolver,
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/openapi/import",
            json={
                "url": "https://spec.example.test/openapi.json",
                "base_url": "https://api.example.test/v1",
            },
        )

    assert response.status_code == 200
    assert response.json()["servers"] == ["https://api.example.test/v1"]
    assert response.json()["operations"][0]["request"]["url"] == (
        "https://api.example.test/v1/items"
    )


def test_import_endpoint_blocks_unapproved_fetch_targets_and_rejects_ambiguous_source() -> None:
    with TestClient(create_app()) as client:
        blocked = client.post(
            "/api/v1/openapi/import",
            json={"url": "http://127.0.0.1:8002/openapi.json"},
        )
        ambiguous = client.post(
            "/api/v1/openapi/import",
            json={"url": "https://example.test/spec.json", "specification": OPENAPI_JSON},
        )

    assert blocked.status_code == 422
    assert "localhost destination is not authorized" in blocked.json()["error"]["message"]
    assert ambiguous.status_code == 422
