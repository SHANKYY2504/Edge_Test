import json
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

import yaml
from pydantic import AnyHttpUrl, ValidationError
from yaml.events import AliasEvent
from yaml.loader import SafeLoader

from app.core.constants import MAX_REQUEST_TIMEOUT_SECONDS
from app.models.boundary import (
    BoundaryDataType,
    BoundaryInput,
    BoundaryLocation,
    BoundaryOperation,
    BoundaryTestCase,
)
from app.models.openapi import (
    OpenApiImportResponse,
    OpenApiOperation,
    OpenApiParameter,
    OpenApiSchema,
)
from app.models.requests import ApiRequest, HttpMethod
from app.services.boundary_generation import generate_boundary_cases

MAX_OPERATIONS = 50
MAX_PARAMETERS_PER_OPERATION = 40
MAX_BODY_FIELDS = 20
MAX_CASES_PER_OPERATION = 100
HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
}


class OpenApiImportError(ValueError):
    """Raised for invalid or unsupported OpenAPI documents."""


class NoYamlAliasesLoader(SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            raise OpenApiImportError("YAML aliases are not supported.")
        return super().compose_node(parent, index)


def parse_specification(text: str) -> Dict[str, Any]:
    if not text.strip():
        raise OpenApiImportError("The specification is empty.")
    if len(text.encode("utf-8")) > 1_048_576:
        raise OpenApiImportError("The specification exceeds the 1 MiB size limit.")
    try:
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = yaml.load(text, Loader=NoYamlAliasesLoader)
    except OpenApiImportError:
        raise
    except (yaml.YAMLError, ValueError, RecursionError) as exception:
        raise OpenApiImportError("The specification could not be parsed as JSON or YAML.") from exception
    if not isinstance(document, dict):
        raise OpenApiImportError("The specification root must be an object.")
    version = document.get("openapi")
    swagger_version = document.get("swagger")
    if not (
        isinstance(version, str)
        and (version.startswith("3.0.") or version.startswith("3.1."))
        or swagger_version == "2.0"
    ):
        raise OpenApiImportError("Only OpenAPI 3.0/3.1 and Swagger 2.0 specifications are supported.")
    if not isinstance(document.get("paths"), dict):
        raise OpenApiImportError("The specification must contain a paths object.")
    return document


def _resolve_schema(
    schema: Any,
    document: Dict[str, Any],
    visited: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    seen = visited or set()
    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise OpenApiImportError("External schema references are not supported.")
        if reference in seen:
            raise OpenApiImportError("Recursive schema references are not supported.")
        target: Any = document
        try:
            for segment in reference[2:].split("/"):
                target = target[segment.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise OpenApiImportError("The specification contains an unresolved schema reference.")
        resolved = _resolve_schema(target, document, seen | {reference})
        return {**resolved, **{key: value for key, value in schema.items() if key != "$ref"}}
    for unsupported in (
        "not",
        "patternProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "prefixItems",
        "contains",
        "minContains",
        "maxContains",
        "propertyNames",
        "dependentSchemas",
        "dependentRequired",
        "if",
        "then",
        "else",
        "multipleOf",
        "minProperties",
        "maxProperties",
        "uniqueItems",
    ):
        if unsupported in schema:
            raise OpenApiImportError(
                "The schema feature '{}' cannot be processed safely.".format(unsupported)
            )
    for composition in ("anyOf", "oneOf"):
        if composition in schema:
            alternatives = schema[composition]
            if (
                not isinstance(alternatives, list)
                or not alternatives
                or not all(isinstance(part, dict) for part in alternatives)
            ):
                raise OpenApiImportError(
                    "The specification has an invalid {} schema.".format(composition)
                )
            result = dict(schema)
            result[composition] = [
                _resolve_schema(part, document, seen) for part in alternatives
            ]
            return result
    if "allOf" in schema:
        all_of = schema["allOf"]
        if not isinstance(all_of, list) or not all(
            isinstance(part, dict) for part in all_of
        ):
            raise OpenApiImportError("The specification has an invalid allOf schema.")
        merged: Dict[str, Any] = {}
        for part in all_of:
            resolved_part = _resolve_schema(part, document, seen)
            merged = _merge_schemas(merged, resolved_part)
        merged = _merge_schemas(
            merged, {key: value for key, value in schema.items() if key != "allOf"}
        )
        return merged
    return schema


def _merge_schemas(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if key == "properties" and isinstance(value, dict):
            existing_properties = merged.get(key, {})
            if not isinstance(existing_properties, dict):
                raise OpenApiImportError("The allOf schema has invalid properties.")
            properties = dict(existing_properties)
            for name, child in value.items():
                if name in properties and isinstance(properties[name], dict) and isinstance(child, dict):
                    properties[name] = {
                        "allOf": [properties[name], child]
                    }
                else:
                    properties[name] = child
            merged[key] = properties
        elif key == "required" and isinstance(value, list):
            existing_required = merged.get(key, [])
            if not isinstance(existing_required, list) or not all(
                isinstance(name, str) for name in [*existing_required, *value]
            ):
                raise OpenApiImportError("The allOf schema has an invalid required list.")
            merged[key] = list(dict.fromkeys([*existing_required, *value]))
        elif key in {"exclusiveMinimum", "exclusiveMaximum"} and isinstance(value, bool):
            existing = merged.get(key)
            if existing is not None and not isinstance(existing, bool):
                raise OpenApiImportError(
                    "The allOf schema mixes incompatible '{}' constraints.".format(key)
                )
            merged[key] = bool(existing) or value
        elif key in {"minimum", "exclusiveMinimum", "minLength", "minItems"}:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise OpenApiImportError("The allOf schema has an invalid '{}' constraint.".format(key))
            existing = merged.get(key)
            if existing is not None and (
                not isinstance(existing, (int, float)) or isinstance(existing, bool)
            ):
                raise OpenApiImportError("The allOf schema has an invalid '{}' constraint.".format(key))
            merged[key] = max(existing, value) if existing is not None else value
        elif key in {"maximum", "exclusiveMaximum", "maxLength", "maxItems"}:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise OpenApiImportError("The allOf schema has an invalid '{}' constraint.".format(key))
            existing = merged.get(key)
            if existing is not None and (
                not isinstance(existing, (int, float)) or isinstance(existing, bool)
            ):
                raise OpenApiImportError("The allOf schema has an invalid '{}' constraint.".format(key))
            merged[key] = min(existing, value) if existing is not None else value
        elif key == "enum" and key in merged:
            if not isinstance(merged[key], list) or not isinstance(value, list):
                raise OpenApiImportError("The allOf schema has an invalid enum constraint.")
            merged[key] = [item for item in merged[key] if item in value]
            if not merged[key]:
                raise OpenApiImportError("The allOf schema has conflicting enum constraints.")
        elif key == "type" and key in merged and merged[key] != value:
            left_types_value = merged[key] if isinstance(merged[key], list) else [merged[key]]
            right_types_value = value if isinstance(value, list) else [value]
            if not all(isinstance(item, str) for item in [*left_types_value, *right_types_value]):
                raise OpenApiImportError("The allOf schema has an invalid type constraint.")
            left_types = set(left_types_value)
            right_types = set(right_types_value)
            intersection = left_types & right_types
            if not intersection:
                raise OpenApiImportError("The allOf schema has incompatible type constraints.")
            merged[key] = next(iter(intersection)) if len(intersection) == 1 else list(intersection)
        elif key == "nullable" and key in merged:
            merged[key] = bool(merged[key]) and bool(value)
        elif key not in merged:
            merged[key] = value
        elif merged[key] != value and key not in {
            "description", "title", "example", "default", "deprecated", "readOnly", "writeOnly"
        }:
            raise OpenApiImportError(
                "Conflicting allOf constraints for '{}' cannot be combined.".format(key)
            )
    return merged


def _schema_type(schema: Dict[str, Any]) -> BoundaryDataType:
    schema_type = schema.get("type")
    if schema_type == "integer":
        return BoundaryDataType.NUMBER
    if schema_type == "number":
        return BoundaryDataType.NUMBER
    if schema_type == "array":
        return BoundaryDataType.ARRAY
    if schema_type == "object" or "properties" in schema:
        return BoundaryDataType.OBJECT
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        if len(non_null) == 1:
            return _schema_type({"type": non_null[0]})
    return BoundaryDataType.STRING


def _representative_value(
    schema: Dict[str, Any],
    document: Dict[str, Any],
    *,
    depth: int = 0,
    visited: Optional[Set[str]] = None,
) -> Any:
    if depth > 8:
        raise OpenApiImportError(
            "Nested JSON schemas deeper than 8 levels are not supported."
        )
    seen = visited or set()
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference in seen:
        raise OpenApiImportError("Recursive schema references are not supported.")
    resolved = _resolve_schema(schema, document, seen)
    next_seen = seen | {reference} if isinstance(reference, str) else seen
    for composition in ("anyOf", "oneOf"):
        alternatives = resolved.get(composition)
        if isinstance(alternatives, list):
            siblings = {key: value for key, value in resolved.items() if key != composition}
            candidates: List[Any] = []
            for alternative in alternatives:
                candidate_schema = _merge_schemas(siblings, alternative)
                candidate = _representative_value(
                    candidate_schema,
                    document,
                    depth=depth + 1,
                    visited=next_seen,
                )
                if _schema_matches_value(candidate, resolved, document, depth=depth + 1):
                    candidates.append(candidate)
            if composition == "anyOf" and candidates:
                return candidates[0]
            if composition == "oneOf":
                unique = [
                    candidate
                    for candidate in candidates
                    if sum(
                        _schema_matches_value(candidate, alternative, document, depth=depth + 1)
                        for alternative in alternatives
                    ) == 1
                ]
                if unique:
                    return unique[0]
                raise OpenApiImportError(
                    "Unable to generate a oneOf value matching exactly one alternative."
                )
            raise OpenApiImportError(
                "Unable to generate an anyOf value matching at least one alternative."
            )
    if "example" in resolved:
        candidate = resolved["example"]
        if _schema_matches_value(candidate, resolved, document, depth=depth + 1):
            return candidate
    if "default" in resolved:
        candidate = resolved["default"]
        if _schema_matches_value(candidate, resolved, document, depth=depth + 1):
            return candidate
    if "const" in resolved:
        return _validated_candidate(resolved["const"], resolved, document)
    if isinstance(resolved.get("enum"), list) and resolved["enum"]:
        for candidate in resolved["enum"]:
            if _schema_matches_value(candidate, resolved, document, depth=depth + 1):
                return candidate
        raise OpenApiImportError("The schema enum has no value satisfying its constraints.")
    if resolved.get("nullable") is True and "type" not in resolved:
        return None
    kind = _schema_type(resolved)
    if kind == BoundaryDataType.NUMBER:
        is_integer = resolved.get("type") == "integer"
        minimum = resolved.get("minimum", 1)
        lower = math.ceil(minimum) if is_integer else minimum
        exclusive_minimum = resolved.get("exclusiveMinimum")
        if exclusive_minimum is True and "minimum" in resolved:
            if is_integer:
                lower = max(lower, math.floor(minimum) + 1)
            else:
                lower = max(lower, math.nextafter(float(minimum), math.inf))
        elif isinstance(exclusive_minimum, (int, float)) and not isinstance(exclusive_minimum, bool):
            if is_integer:
                lower = max(lower, math.floor(exclusive_minimum) + 1)
            else:
                lower = max(lower, math.nextafter(float(exclusive_minimum), math.inf))
        candidate = lower
        maximum = resolved.get("maximum")
        exclusive_maximum = resolved.get("exclusiveMaximum")
        if exclusive_maximum is True and "maximum" in resolved:
            if is_integer:
                maximum = min(maximum, math.ceil(maximum) - 1)
            else:
                maximum = min(
                    maximum, math.nextafter(float(maximum), -math.inf)
                )
        elif isinstance(exclusive_maximum, (int, float)) and not isinstance(exclusive_maximum, bool):
            if is_integer:
                upper_bound = math.ceil(exclusive_maximum) - 1
            else:
                upper_bound = math.nextafter(float(exclusive_maximum), -math.inf)
            maximum = min(maximum, upper_bound) if maximum is not None else upper_bound
        if maximum is not None and candidate > maximum:
            raise OpenApiImportError(
                "The numeric schema constraints have no representative value."
            )
        return _validated_candidate(candidate, resolved, document)
    if resolved.get("type") == "boolean":
        return _validated_candidate(True, resolved, document)
    if kind == BoundaryDataType.ARRAY:
        item_schema = _resolve_schema(resolved.get("items", {}), document)
        minimum_items = resolved.get("minItems", 1)
        maximum_items = resolved.get("maxItems", 100)
        if not isinstance(minimum_items, int) or not isinstance(maximum_items, int):
            raise OpenApiImportError("The array schema has invalid item-count constraints.")
        if minimum_items > 100:
            raise OpenApiImportError(
                "The array schema requires more than 100 representative items."
            )
        item_count = min(max(minimum_items, 1), maximum_items)
        candidate = [
            _representative_value(
                item_schema, document, depth=depth + 1, visited=next_seen
            )
            for _ in range(item_count)
        ]
        return _validated_candidate(candidate, resolved, document)
    if kind == BoundaryDataType.OBJECT:
        required = resolved.get("required", [])
        properties = resolved.get("properties", {})
        if not isinstance(required, list) or not isinstance(properties, dict):
            return {}
        candidate = {
            name: _representative_value(
                properties[name],
                document,
                depth=depth + 1,
                visited=next_seen,
            )
            for name in required
            if name in properties and isinstance(properties[name], dict)
        }
        return _validated_candidate(candidate, resolved, document)
    candidate = "boundary"
    if kind == BoundaryDataType.STRING:
        minimum_length = resolved.get("minLength", 0)
        maximum_length = resolved.get("maxLength", float("inf"))
        if not isinstance(minimum_length, int) or not isinstance(maximum_length, (int, float)):
            raise OpenApiImportError("The string schema has invalid length constraints.")
        if len(candidate) < minimum_length:
            candidate = "x" * minimum_length
        if len(candidate) > maximum_length:
            candidate = "x" * minimum_length
    if resolved.get("nullable") is True and resolved.get("type") in (None, "null"):
        return None
    return _validated_candidate(candidate, resolved, document)


def _validated_candidate(value: Any, schema: Dict[str, Any], document: Dict[str, Any]) -> Any:
    if not _schema_matches_value(value, schema, document):
        raise OpenApiImportError(
            "Unable to generate a representative value satisfying the schema constraints."
        )
    return value


def _schema_matches_value(
    value: Any,
    schema: Any,
    document: Dict[str, Any],
    *,
    depth: int = 0,
) -> bool:
    if depth > 12:
        raise OpenApiImportError("Schema composition is nested deeper than supported.")
    resolved = _resolve_schema(schema, document)
    if "allOf" in resolved and not all(
        _schema_matches_value(value, part, document, depth=depth + 1)
        for part in resolved["allOf"]
    ):
        return False
    if "anyOf" in resolved and not any(
        _schema_matches_value(value, part, document, depth=depth + 1)
        for part in resolved["anyOf"]
    ):
        return False
    if "oneOf" in resolved and sum(
        _schema_matches_value(value, part, document, depth=depth + 1)
        for part in resolved["oneOf"]
    ) != 1:
        return False
    schema_type = resolved.get("type")
    accepted_types = schema_type if isinstance(schema_type, list) else [schema_type]
    if schema_type is not None:
        if value is None:
            is_nullable = resolved.get("nullable") is True or "null" in accepted_types
            if not is_nullable:
                return False
        elif not any(
            _matches_json_type(value, candidate_type)
            for candidate_type in accepted_types
        ):
            return False
    if "const" in resolved and value != resolved["const"]:
        return False
    if isinstance(resolved.get("enum"), list) and value not in resolved["enum"]:
        return False
    if isinstance(value, dict):
        required = resolved.get("required", [])
        properties = resolved.get("properties", {})
        if any(name not in value for name in required if isinstance(name, str)):
            return False
        if isinstance(properties, dict):
            for name, child_schema in properties.items():
                if name in value and not _schema_matches_value(
                    value[name], child_schema, document, depth=depth + 1
                ):
                    return False
    if isinstance(value, list):
        if len(value) < resolved.get("minItems", 0):
            return False
        if len(value) > resolved.get("maxItems", float("inf")):
            return False
        if isinstance(resolved.get("items"), dict) and not all(
            _schema_matches_value(item, resolved["items"], document, depth=depth + 1)
            for item in value
        ):
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in resolved and value < resolved["minimum"]:
            return False
        if "maximum" in resolved and value > resolved["maximum"]:
            return False
        if (
            "exclusiveMinimum" in resolved
            and isinstance(resolved["exclusiveMinimum"], (int, float))
            and not isinstance(resolved["exclusiveMinimum"], bool)
        ):
            if value <= resolved["exclusiveMinimum"]:
                return False
        if resolved.get("exclusiveMinimum") is True and "minimum" in resolved:
            if value <= resolved["minimum"]:
                return False
        if (
            "exclusiveMaximum" in resolved
            and isinstance(resolved["exclusiveMaximum"], (int, float))
            and not isinstance(resolved["exclusiveMaximum"], bool)
        ):
            if value >= resolved["exclusiveMaximum"]:
                return False
        if resolved.get("exclusiveMaximum") is True and "maximum" in resolved:
            if value >= resolved["maximum"]:
                return False
    if isinstance(value, str):
        if len(value) < resolved.get("minLength", 0):
            return False
        if len(value) > resolved.get("maxLength", float("inf")):
            return False
        pattern = resolved.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    return False
            except re.error as exception:
                raise OpenApiImportError("The schema contains an invalid regex pattern.") from exception
    return True


def _matches_json_type(value: Any, schema_type: Any) -> bool:
    if schema_type == "null":
        return value is None
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _flatten_body_schema(
    schema: Dict[str, Any],
    document: Dict[str, Any],
    *,
    prefix: str = "",
    depth: int = 0,
    visited: Optional[Set[str]] = None,
) -> List[Tuple[str, Dict[str, Any], bool]]:
    if depth > 8:
        raise OpenApiImportError("Nested JSON schemas deeper than 8 levels are not supported.")
    seen = visited or set()
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference in seen:
        raise OpenApiImportError("Recursive schema references are not supported.")
    resolved = _resolve_schema(schema, document, seen)
    next_seen = seen | {reference} if isinstance(reference, str) else seen
    properties = resolved.get("properties")
    required = resolved.get("required", [])
    if not isinstance(properties, dict) or not properties:
        return [(prefix or "__root__", resolved, True)]
    flattened: List[Tuple[str, Dict[str, Any], bool]] = []
    for name, child_schema in properties.items():
        if not isinstance(child_schema, dict):
            continue
        path = "{}.{}".format(prefix, name) if prefix else name
        child_reference = child_schema.get("$ref")
        if isinstance(child_reference, str) and child_reference in next_seen:
            raise OpenApiImportError("Recursive schema references are not supported.")
        child_visited = (
            next_seen | {child_reference}
            if isinstance(child_reference, str)
            else next_seen
        )
        child = _resolve_schema(child_schema, document, next_seen)
        is_required = name in required if isinstance(required, list) else False
        if child.get("type") == "object" and isinstance(child.get("properties"), dict):
            flattened.append((path, child, is_required))
            nested = _flatten_body_schema(
                child,
                document,
                prefix=path,
                depth=depth + 1,
                visited=child_visited,
            )
            flattened.extend(
                (nested_name, nested_schema, is_required or nested_required)
                for nested_name, nested_schema, nested_required in nested
            )
        else:
            flattened.append((path, child, is_required))
    return flattened


def _validated_server_url(server: Dict[str, Any]) -> str:
    server_url = server.get("url")
    if not isinstance(server_url, str):
        raise OpenApiImportError("The specification contains an invalid server URL.")
    variables = server.get("variables", {})
    if "{" in server_url:
        for name in re.findall(r"\{([^{}]+)\}", server_url):
            variable = variables.get(name, {}) if isinstance(variables, dict) else {}
            default = variable.get("default") if isinstance(variable, dict) else None
            if not isinstance(default, str):
                raise OpenApiImportError(
                    "Server variable '{}' must define a string default.".format(name)
                )
            server_url = server_url.replace("{" + name + "}", default)
    try:
        parsed = AnyHttpUrl(server_url)
    except ValidationError as exception:
        raise OpenApiImportError("The specification contains an invalid server URL.") from exception
    if parsed.username is not None or parsed.password is not None:
        raise OpenApiImportError("Server URLs containing credentials are not allowed.")
    if parsed.query is not None or parsed.fragment is not None:
        raise OpenApiImportError("Server URLs must not contain a query or fragment.")
    return str(parsed).rstrip("/")


def _document_servers(document: Dict[str, Any], base_url: Optional[str]) -> List[str]:
    servers = document.get("servers")
    if isinstance(servers, list) and servers:
        if not all(isinstance(server, dict) for server in servers):
            raise OpenApiImportError("The specification contains an invalid server definition.")
        return list(dict.fromkeys(_validated_server_url(server) for server in servers))
    if document.get("swagger") == "2.0":
        host = document.get("host")
        if isinstance(host, str):
            schemes = document.get("schemes")
            scheme = schemes[0] if isinstance(schemes, list) and schemes else "https"
            base_path = document.get("basePath", "")
            return [_validated_server_url({"url": "{}://{}{}".format(scheme, host, base_path)})]
    if base_url:
        return [_validated_server_url({"url": base_url})]
    return []


def _server_url(
    document: Dict[str, Any],
    operation: Dict[str, Any],
    base_url: Optional[str] = None,
) -> str:
    operation_servers = operation.get("servers")
    if isinstance(operation_servers, list) and operation_servers:
        server = operation_servers[0]
        if isinstance(server, dict):
            return _validated_server_url(server)
    document_servers = document.get("servers")
    if isinstance(document_servers, list) and document_servers:
        server = document_servers[0]
        if isinstance(server, dict):
            return _validated_server_url(server)
    if document.get("swagger") == "2.0":
        servers = _document_servers(document, base_url)
        if servers:
            return servers[0]
    if base_url:
        return _validated_server_url({"url": base_url})
    raise OpenApiImportError(
        "No server URL is defined for this operation. Provide a Base URL to import this specification."
    )


def _operation_parameters(
    operation: Dict[str, Any],
    path_item: Dict[str, Any],
    document: Dict[str, Any],
) -> List[Dict[str, Any]]:
    combined: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for parameter in [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]:
        if not isinstance(parameter, dict):
            continue
        if "$ref" in parameter:
            reference = parameter["$ref"]
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise OpenApiImportError("External parameter references are not supported.")
            target: Any = document
            try:
                for segment in reference[2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError):
                raise OpenApiImportError("The specification contains an unresolved parameter reference.")
            if not isinstance(target, dict):
                raise OpenApiImportError("The specification contains an invalid parameter reference.")
            parameter = target
        name, location = parameter.get("name"), parameter.get("in")
        if not isinstance(name, str) or location not in {"path", "query", "header"}:
            continue
        schema = parameter.get("schema")
        if schema is None and document.get("swagger") == "2.0":
            schema = {
                key: value
                for key, value in parameter.items()
                if key
                in {
                    "type",
                    "format",
                    "items",
                    "enum",
                    "default",
                    "example",
                    "minimum",
                    "maximum",
                    "exclusiveMinimum",
                    "exclusiveMaximum",
                    "minLength",
                    "maxLength",
                    "pattern",
                }
            }
        if not isinstance(schema, dict):
            schema = {}
        combined[(location, name)] = {
            "name": name,
            "location": location,
            "required": bool(parameter.get("required")) or location == "path",
            "schema": _resolve_schema(schema, document),
            "style": parameter.get("style"),
            "explode": parameter.get("explode"),
        }
    return list(combined.values())


def _get_request_body(
    operation: Dict[str, Any], document: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    body = operation.get("requestBody")
    if body is None and document.get("swagger") == "2.0":
        body_parameters = [
            parameter
            for parameter in operation.get("parameters", [])
            if isinstance(parameter, dict) and parameter.get("in") == "body"
        ]
        if body_parameters:
            schema = body_parameters[0].get("schema", {})
            return {"application/json": {"schema": schema}}, _resolve_schema(schema, document)
    if not isinstance(body, dict):
        return None, None
    if "$ref" in body:
        reference = body["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise OpenApiImportError("External request body references are not supported.")
        target: Any = document
        try:
            for segment in reference[2:].split("/"):
                target = target[segment.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise OpenApiImportError("The specification contains an unresolved request body reference.")
        if not isinstance(target, dict):
            raise OpenApiImportError("The specification contains an invalid request body reference.")
        body = target
    content = body.get("content", {})
    if not isinstance(content, dict):
        return None, None
    media_type = next(
        (name for name in content if name == "application/json" or name.endswith("+json")),
        None,
    )
    if media_type is None:
        return None, None
    media = content[media_type]
    schema = media.get("schema", {}) if isinstance(media, dict) else {}
    if not isinstance(schema, dict):
        schema = {}
    return content, _resolve_schema(schema, document)


def _operation_cases(
    inputs: List[BoundaryInput],
    document: Dict[str, Any],
    body_schema: Optional[Dict[str, Any]],
    has_body: bool,
    path_values: Dict[str, Any],
) -> List[BoundaryTestCase]:
    cases: List[BoundaryTestCase] = []
    if has_body:
        valid_case = BoundaryTestCase(
            id="valid-request",
            label="valid representative request",
            input_name="__root__",
            location=BoundaryLocation.BODY,
            data_type=BoundaryDataType.OBJECT,
            value=_representative_value(body_schema or {}, document),
            path_values=path_values,
        )
    else:
        valid_case = BoundaryTestCase(
            id="valid-request",
            label="valid representative request",
            input_name="__no_inputs__",
            location=BoundaryLocation.QUERY,
            data_type=BoundaryDataType.STRING,
            operation=BoundaryOperation.REMOVE,
            value=None,
            path_values=path_values,
        )
    cases.append(valid_case)
    for input_index, boundary_input in enumerate(inputs):
        generated = generate_boundary_cases([boundary_input])
        for case_index, case in enumerate(generated):
            cases.append(
                case.model_copy(
                    update={"id": "input-{}-case-{}".format(input_index + 1, case_index + 1)}
                )
            )
        if boundary_input.location in {
            BoundaryLocation.PATH,
            BoundaryLocation.QUERY,
            BoundaryLocation.HEADER,
        }:
            cases.append(
                BoundaryTestCase(
                    id="missing-input-{}".format(input_index + 1),
                    label="missing parameter: {}".format(boundary_input.name),
                    input_name=boundary_input.name,
                    location=boundary_input.location,
                    data_type=boundary_input.data_type,
                    operation=BoundaryOperation.REMOVE,
                    value=None,
                )
            )
        if boundary_input.location == BoundaryLocation.BODY and boundary_input.name != "__root__":
            cases.append(
                BoundaryTestCase(
                    id="missing-{}".format(re.sub(r"[^A-Za-z0-9_-]", "-", boundary_input.name)),
                    label="missing field: {}".format(boundary_input.name),
                    input_name=boundary_input.name,
                    location=BoundaryLocation.BODY,
                    data_type=boundary_input.data_type,
                    operation=BoundaryOperation.REMOVE,
                    value=None,
                )
            )
    if len(cases) > MAX_CASES_PER_OPERATION:
        return cases[:MAX_CASES_PER_OPERATION]
    return cases


def parse_openapi_document(
    document: Dict[str, Any],
    base_url: Optional[str] = None,
) -> OpenApiImportResponse:
    title, version = "Imported API", "unknown"
    info = document.get("info")
    if isinstance(info, dict):
        title = info.get("title") if isinstance(info.get("title"), str) else title
        version = info.get("version") if isinstance(info.get("version"), str) else version
    specification_version = document.get("openapi", "Swagger {}".format(document.get("swagger")))
    operations: List[OpenApiOperation] = []
    global_warnings: List[str] = []
    servers = _document_servers(document, base_url)
    for path, path_item in document["paths"].items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        if not path.startswith("/") or path.startswith("//"):
            global_warnings.append("Skipped a path that was not a safe relative API path.")
            continue
        for method_name, operation in path_item.items():
            if method_name.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if len(operations) >= MAX_OPERATIONS:
                global_warnings.append("Only the first 200 operations are included.")
                break
            method = HttpMethod(method_name.upper())
            server = _server_url(document, operation, base_url)
            # Joining by string prevents a path beginning with // from replacing the server host.
            request_url = server + "/" + path.lstrip("/")
            parsed_url = urlsplit(request_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise OpenApiImportError("An operation generated an invalid request URL.")

            raw_parameters = _operation_parameters(operation, path_item, document)
            path_parameters = set(
                re.findall(r"\{([^{}]+)\}", path)
            )
            declared_path_parameters = {
                parameter["name"]
                for parameter in raw_parameters
                if parameter["location"] == "path"
            }
            if path_parameters - declared_path_parameters:
                raise OpenApiImportError(
                    "Path '{}' is missing parameter definitions for: {}.".format(
                        path, ", ".join(sorted(path_parameters - declared_path_parameters))
                    )
                )
            if len(raw_parameters) > MAX_PARAMETERS_PER_OPERATION:
                raise OpenApiImportError(
                    "Operation '{}' has more than 40 parameters.".format(path)
                )
            headers: Dict[str, str] = {"Accept": "application/json"}
            query: Dict[str, str] = {}
            path_values: Dict[str, Any] = {}
            parameters: List[OpenApiParameter] = []
            inputs: List[BoundaryInput] = []
            warnings: List[str] = []
            for parameter in raw_parameters:
                data_type = _schema_type(parameter["schema"])
                if data_type in {BoundaryDataType.ARRAY, BoundaryDataType.OBJECT}:
                    warnings.append(
                        "Complex {} parameter '{}' is approximated as a string.".format(
                            parameter["location"], parameter["name"]
                        )
                    )
                    data_type = BoundaryDataType.STRING
                location = BoundaryLocation(parameter["location"])
                parameters.append(
                    OpenApiParameter(
                        name=parameter["name"],
                        location=parameter["location"],
                        required=parameter["required"],
                        schema=parameter["schema"],
                    )
                )
                default_value = _representative_value(parameter["schema"], document)
                if location == BoundaryLocation.PATH:
                    path_values[parameter["name"]] = default_value
                elif location == BoundaryLocation.QUERY:
                    query[parameter["name"]] = (
                        str(default_value).lower()
                        if isinstance(default_value, bool)
                        else str(default_value)
                    )
                elif location == BoundaryLocation.HEADER:
                    if parameter["name"].lower() in {
                        "host",
                        "proxy-authorization",
                        "proxy-connection",
                    }:
                        warnings.append(
                            "The restricted header '{}' is not generated.".format(
                                parameter["name"]
                            )
                        )
                        continue
                    if parameter["name"].lower() not in {"authorization", "cookie"}:
                        headers[parameter["name"]] = (
                            str(default_value).lower()
                            if isinstance(default_value, bool)
                            else str(default_value)
                        )
                inputs.append(
                    BoundaryInput(
                        name=parameter["name"],
                        location=location,
                        data_type=data_type,
                        minimum=parameter["schema"].get(
                            "minimum", parameter["schema"].get("exclusiveMinimum")
                        ),
                        maximum=parameter["schema"].get(
                            "maximum", parameter["schema"].get("exclusiveMaximum")
                        ),
                    )
                )
                style = parameter.get("style")
                if style not in {None, "simple", "form", "matrix", "label", "spaceDelimited", "pipeDelimited", "deepObject"}:
                    warnings.append(
                        "Parameter style '{}' is not supported.".format(style)
                    )
            request_body_content, body_schema = _get_request_body(operation, document)
            if (
                operation.get("requestBody") is not None
                and body_schema is None
                and isinstance(request_body_content, dict)
            ):
                warnings.append(
                    "Only application/json request bodies are automatically generated."
                )
            if operation.get("security") or document.get("security"):
                warnings.append(
                    "Security schemes are not automatically populated; configure auth manually."
                )
            has_body = body_schema is not None
            if body_schema is not None:
                body_fields = _flatten_body_schema(body_schema, document)
                if len(body_fields) > MAX_BODY_FIELDS:
                    warnings.append("Only the first 20 JSON body fields are tested.")
                    body_fields = body_fields[:MAX_BODY_FIELDS]
                if not body_fields:
                    body_fields = [("__root__", body_schema, True)]
                body: Any = _representative_value(body_schema, document)
                if isinstance(body, dict):
                    for field_name, schema, _ in body_fields:
                        if field_name == "__root__":
                            continue
                        value = _representative_value(schema, document)
                        parts = field_name.split(".")
                        parent = body
                        for part in parts[:-1]:
                            if not isinstance(parent.get(part), dict):
                                parent[part] = {}
                            parent = parent[part]
                        parent[parts[-1]] = value
                elif body_schema.get("type") == "object":
                    body = {}
                if body_schema.get("type") == "object":
                    inputs.append(
                        BoundaryInput(
                            name="__root__",
                            location=BoundaryLocation.BODY,
                            data_type=BoundaryDataType.OBJECT,
                        )
                    )
                    for field_name, field_schema, _ in body_fields:
                        if field_name == "__root__":
                            continue
                        inputs.append(
                            BoundaryInput(
                                name=field_name,
                                location=BoundaryLocation.BODY,
                                data_type=_schema_type(field_schema),
                                minimum=field_schema.get(
                                    "minimum", field_schema.get("exclusiveMinimum")
                                ),
                                maximum=field_schema.get(
                                    "maximum", field_schema.get("exclusiveMaximum")
                                ),
                            )
                        )
                else:
                    inputs.append(
                        BoundaryInput(
                            name="__root__",
                            location=BoundaryLocation.BODY,
                            data_type=_schema_type(body_schema),
                            minimum=body_schema.get("minimum", body_schema.get("exclusiveMinimum")),
                            maximum=body_schema.get("maximum", body_schema.get("exclusiveMaximum")),
                        )
                    )
            else:
                body = None

            request = ApiRequest(
                url=request_url,
                method=method,
                headers=headers,
                query_params=query,
                body=body,
                timeout=min(10.0, MAX_REQUEST_TIMEOUT_SECONDS),
            )
            cases = _operation_cases(
                inputs, document, body_schema, has_body, path_values
            )
            if len(cases) == MAX_CASES_PER_OPERATION:
                warnings.append("Generated cases are capped at 100 per operation.")
            operations.append(
                OpenApiOperation(
                    id=operation.get("operationId")
                    if isinstance(operation.get("operationId"), str)
                    else "{}_{}".format(method.value.lower(), path.strip("/").replace("/", "_")),
                    method=method,
                    path=path,
                    summary=operation.get("summary")
                    if isinstance(operation.get("summary"), str)
                    else operation.get("description", path),
                    tags=operation.get("tags", []) if isinstance(operation.get("tags"), list) else [],
                    request=request,
                    parameters=parameters,
                    request_body_schema=body_schema,
                    cases=cases,
                    warnings=list(dict.fromkeys(warnings)),
                )
            )
        if len(operations) >= MAX_OPERATIONS:
            break
    if not operations:
        raise OpenApiImportError("No supported HTTP operations were found in the specification.")
    components = document.get("components")
    schema_definitions = (
        components.get("schemas", {})
        if isinstance(components, dict)
        else document.get("definitions", {})
    )
    if not isinstance(schema_definitions, dict):
        raise OpenApiImportError("The specification schemas section must be an object.")
    return OpenApiImportResponse(
        title=title,
        version=version,
        specification_version=specification_version,
        operations=operations,
        servers=servers,
        schemas=[
            OpenApiSchema(name=name, schema_value=schema)
            for name, schema in schema_definitions.items()
            if isinstance(name, str)
        ],
        warnings=global_warnings,
    )


def import_openapi_text(
    text: str,
    base_url: Optional[str] = None,
) -> OpenApiImportResponse:
    try:
        return parse_openapi_document(parse_specification(text), base_url=base_url)
    except OpenApiImportError:
        raise
    except ValidationError as exception:
        raise OpenApiImportError(
            "The specification contains values that cannot be used to generate requests."
        ) from exception
