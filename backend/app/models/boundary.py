import re
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

BoundaryValue = JsonValue


class BoundaryLocation(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    BODY = "body"


class BoundaryDataType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    ARRAY = "array"
    OBJECT = "object"


class BoundaryInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: BoundaryLocation
    data_type: BoundaryDataType
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_name_and_bounds(self) -> "BoundaryInput":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", self.name):
            raise ValueError("Input names must use letters, digits, dots, dashes, or underscores.")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Minimum must not exceed maximum.")
        if (
            self.location != BoundaryLocation.BODY
            and self.data_type in {BoundaryDataType.ARRAY, BoundaryDataType.OBJECT}
        ):
            raise ValueError("Arrays and objects can only be tested in JSON body fields.")
        return self


class BoundaryGenerationRequest(BaseModel):
    inputs: List[BoundaryInput] = Field(min_length=1, max_length=20)

    model_config = ConfigDict(extra="forbid")


class BoundaryOperation(str, Enum):
    SET = "set"
    REMOVE = "remove"


class BoundaryTestCase(BaseModel):
    id: str
    label: str
    input_name: str
    location: BoundaryLocation
    data_type: BoundaryDataType
    operation: BoundaryOperation = BoundaryOperation.SET
    value: BoundaryValue = None
    path_values: Dict[str, JsonValue] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class BoundaryGenerationResponse(BaseModel):
    cases: List[BoundaryTestCase] = Field(max_length=500)

    model_config = ConfigDict(extra="forbid")
