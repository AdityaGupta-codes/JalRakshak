from typing import List, Literal

from pydantic import BaseModel, Field

Algorithm = Literal["fcfs", "priority", "round_robin", "water_aware"]


class RequestCreate(BaseModel):
    user_id: int
    reservoir_id: int = 1
    requested_litres: float = Field(gt=0)
    urgency: int = Field(default=3, ge=1, le=5)
    deadline_in_minutes: float = Field(default=240, gt=0)


class SimulateBody(BaseModel):
    reservoir_id: int = 1
    algorithms: List[Algorithm] = ["fcfs"]


class CommitBody(BaseModel):
    reservoir_id: int = 1
    algorithm: Algorithm = "fcfs"
