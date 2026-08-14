"""Shared Pydantic base model for ORM-backed read schemas."""

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
