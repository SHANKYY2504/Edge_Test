from fastapi.testclient import TestClient

from app.main import create_app
from app.models.boundary import (
    BoundaryDataType,
    BoundaryInput,
    BoundaryLocation,
    BoundaryOperation,
)
from app.services.boundary_generation import generate_boundary_cases


def test_generates_string_boundary_cases() -> None:
    cases = generate_boundary_cases(
        [
            BoundaryInput(
                name="display_name",
                location=BoundaryLocation.BODY,
                data_type=BoundaryDataType.STRING,
            )
        ]
    )

    assert [case.label for case in cases] == [
        "empty string",
        "very long string (4096 characters)",
        "special characters",
        "whitespace",
        "invalid number type",
        "null value",
    ]
    assert cases[0].value == ""
    assert len(cases[1].value) == 4096
    assert cases[4].value == 42
    assert "\t" in cases[3].value
    assert "\n" in cases[3].value


def test_generates_number_boundaries_from_optional_limits() -> None:
    cases = generate_boundary_cases(
        [
            BoundaryInput(
                name="age",
                location=BoundaryLocation.QUERY,
                data_type=BoundaryDataType.NUMBER,
                minimum=18,
                maximum=120,
            )
        ]
    )

    assert [case.value for case in cases[:6]] == [0, -1, 18, 120, 17, 121]
    assert cases[6].label == "malformed number"
    assert cases[6].value == "not-a-number"


def test_generates_array_and_object_cases_including_removal() -> None:
    cases = generate_boundary_cases(
        [
            BoundaryInput(
                name="items",
                location=BoundaryLocation.BODY,
                data_type=BoundaryDataType.ARRAY,
            ),
            BoundaryInput(
                name="profile.email",
                location=BoundaryLocation.BODY,
                data_type=BoundaryDataType.OBJECT,
            ),
        ]
    )

    assert cases[2].value == ["boundary"] * 100
    object_cases = cases[5:]
    missing = next(case for case in object_cases if case.label == "missing field")
    assert missing.operation == BoundaryOperation.REMOVE
    assert next(case for case in object_cases if case.label == "null value").value is None
    assert next(case for case in object_cases if case.label == "null field value").value == {
        "boundaryField": None
    }
    assert next(case for case in object_cases if case.label == "unexpected field").value == {
        "unexpectedBoundaryField": "boundary"
    }


def test_boundary_generation_endpoint_validates_and_returns_cases() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/boundary/generate",
            json={
                "inputs": [
                    {
                        "name": "id",
                        "location": "path",
                        "data_type": "number",
                        "minimum": 1,
                        "maximum": 9,
                    }
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()["cases"]
    assert payload[0]["input_name"] == "id"
    assert payload[0]["location"] == "path"
    assert len(payload) == 8


def test_boundary_generation_rejects_invalid_name_bounds_and_oversized_input() -> None:
    with TestClient(create_app()) as client:
        invalid_name = client.post(
            "/api/v1/boundary/generate",
            json={
                "inputs": [
                    {"name": "$id", "location": "path", "data_type": "number"}
                ]
            },
        )
        invalid_bounds = client.post(
            "/api/v1/boundary/generate",
            json={
                "inputs": [
                    {
                        "name": "age",
                        "location": "body",
                        "data_type": "number",
                        "minimum": 10,
                        "maximum": 1,
                    }
                ]
            },
        )
        too_many = client.post(
            "/api/v1/boundary/generate",
            json={
                "inputs": [
                    {
                        "name": "field{}".format(index),
                        "location": "body",
                        "data_type": "string",
                    }
                    for index in range(21)
                ]
            },
        )

    assert invalid_name.status_code == 422
    assert invalid_bounds.status_code == 422
    assert too_many.status_code == 422
