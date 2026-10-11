from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Algorithm = Literal["fcfs", "priority", "round_robin", "water_aware"]


class RequestCreate(BaseModel):
    user_id: int
    reservoir_id: int = 1
    requested_litres: float = Field(gt=0, le=10_000_000)
    urgency: int = Field(default=3, ge=1, le=5)
    deadline_in_minutes: float = Field(default=240, gt=0, le=100_000)


class SimulateBody(BaseModel):
    reservoir_id: int = 1
    algorithms: List[Algorithm] = ["fcfs", "priority", "round_robin", "water_aware"]


class CommitBody(BaseModel):
    reservoir_id: int = 1
    algorithm: Algorithm = "fcfs"


class ResetBody(BaseModel):
    reservoir_id: int = 1
    available_litres: Optional[float] = Field(default=None, ge=0)  # None: 50,000 L (or capacity if smaller)


UserType = Literal["hospital", "domestic", "school", "industry", "commercial", "park"]


class ReservoirUpdate(BaseModel):
    """Only the fields that are sent are changed."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    capacity_litres: Optional[float] = Field(default=None, gt=0, le=1e10)
    available_litres: Optional[float] = Field(default=None, ge=0, le=1e10)


class ZoneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    population: int = Field(gt=0, le=100_000_000)


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    population: Optional[int] = Field(default=None, gt=0, le=100_000_000)


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    user_type: UserType
    priority: int = Field(ge=1, le=5)
    zone_id: int


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    user_type: Optional[UserType] = None
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    zone_id: Optional[int] = None
