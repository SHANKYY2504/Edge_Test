from typing import List, Tuple

from app.models.boundary import (
    BoundaryDataType,
    BoundaryInput,
    BoundaryOperation,
    BoundaryTestCase,
)

LONG_STRING_LENGTH = 4_096
LARGE_ARRAY_LENGTH = 100


def _cases_for_input(
    boundary_input: BoundaryInput,
) -> List[Tuple[str, BoundaryOperation, object]]:
    data_type = boundary_input.data_type
    if data_type == BoundaryDataType.STRING:
        return [
            ("empty string", BoundaryOperation.SET, ""),
            ("very long string (4096 characters)", BoundaryOperation.SET, "x" * LONG_STRING_LENGTH),
            ("special characters", BoundaryOperation.SET, "'\"<> & /? # % \\\\"),
            ("whitespace", BoundaryOperation.SET, " \t\n "),
            ("invalid number type", BoundaryOperation.SET, 42),
            ("null value", BoundaryOperation.SET, None),
        ]
    if data_type == BoundaryDataType.NUMBER:
        minimum = boundary_input.minimum if boundary_input.minimum is not None else 1
        maximum = boundary_input.maximum if boundary_input.maximum is not None else 100
        return [
            ("zero", BoundaryOperation.SET, 0),
            ("negative number", BoundaryOperation.SET, -1),
            ("minimum", BoundaryOperation.SET, minimum),
            ("maximum", BoundaryOperation.SET, maximum),
            ("below minimum", BoundaryOperation.SET, minimum - 1),
            ("above maximum", BoundaryOperation.SET, maximum + 1),
            ("malformed number", BoundaryOperation.SET, "not-a-number"),
            ("null value", BoundaryOperation.SET, None),
        ]
    if data_type == BoundaryDataType.ARRAY:
        return [
            ("empty array", BoundaryOperation.SET, []),
            ("single element", BoundaryOperation.SET, ["boundary"]),
            (
                "large array (100 elements)",
                BoundaryOperation.SET,
                ["boundary"] * LARGE_ARRAY_LENGTH,
            ),
            ("invalid string type", BoundaryOperation.SET, "not-an-array"),
            ("null value", BoundaryOperation.SET, None),
        ]
    if data_type == BoundaryDataType.OBJECT:
        return [
            ("empty object", BoundaryOperation.SET, {}),
            ("missing field", BoundaryOperation.REMOVE, None),
            ("null value", BoundaryOperation.SET, None),
            ("null field value", BoundaryOperation.SET, {"boundaryField": None}),
            (
                "unexpected field",
                BoundaryOperation.SET,
                {"unexpectedBoundaryField": "boundary"},
            ),
            ("invalid array type", BoundaryOperation.SET, []),
            ("invalid string type", BoundaryOperation.SET, "not-an-object"),
        ]
    raise ValueError("Unsupported boundary data type.")


def generate_boundary_cases(inputs: List[BoundaryInput]) -> List[BoundaryTestCase]:
    cases: List[BoundaryTestCase] = []
    for input_index, boundary_input in enumerate(inputs):
        for case_index, (label, operation, value) in enumerate(
            _cases_for_input(boundary_input)
        ):
            cases.append(
                BoundaryTestCase(
                    id="input-{}-case-{}".format(input_index + 1, case_index + 1),
                    label=label,
                    input_name=boundary_input.name,
                    location=boundary_input.location,
                    data_type=boundary_input.data_type,
                    operation=operation,
                    value=value,
                )
            )
    return cases
