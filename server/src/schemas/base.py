import uuid

from pydantic import BaseModel, ConfigDict, field_serializer


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BaseOut(BaseSchema):
    id: uuid.UUID

    @field_serializer("id")
    def serialize_id(self, value: uuid.UUID) -> str:
        return str(value)
