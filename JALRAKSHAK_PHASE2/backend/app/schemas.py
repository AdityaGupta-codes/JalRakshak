from datetime import date
from typing import Any, Dict, List, Literal, Optional

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
    # officer override: {request_id: litres} changed from the computed plan, with the reason (decision record)
    overrides: Optional[Dict[int, float]] = None
    reason: Optional[str] = Field(default=None, max_length=1000)


class ShadowBody(BaseModel):
    reservoir_id: int = 1
    algorithm: Algorithm = "water_aware"
    note: Optional[str] = Field(default=None, max_length=1000)


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
    priority: Optional[int] = Field(default=None, ge=1, le=5)  # None: from the active policy's class
    zone_id: int


class UserUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    user_type: Optional[UserType] = None
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    zone_id: Optional[int] = None
    reset_priority: bool = False  # go back to the priority of the organisation's class in the policy


# ---------- sign-in ----------
UserTypeLiteral = Literal["hospital", "domestic", "school", "industry", "commercial", "park"]


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=128)


class RegisterBody(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=30)
    organisation_name: str = Field(min_length=1, max_length=100)
    organisation_type: UserTypeLiteral
    zone_id: int
    consent: bool = False  # accepted the privacy notice (required)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ---------- user panel ----------
class MyRequestCreate(BaseModel):
    requested_litres: float = Field(gt=0, le=10_000_000)
    urgency: int = Field(default=3, ge=1, le=5)
    deadline_in_minutes: float = Field(default=240, gt=0, le=100_000)
    note: Optional[str] = Field(default=None, max_length=500)


IssueCategory = Literal["leak", "no_supply", "low_pressure", "quality", "other"]


class IssueCreate(BaseModel):
    category: IssueCategory
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    location: Optional[str] = Field(default=None, max_length=200)


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)


# ---------- admin panel ----------
Role = Literal["admin", "requester"]


class AccountCreate(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    full_name: str = Field(min_length=1, max_length=100)
    role: Role
    user_id: Optional[int] = None
    phone: Optional[str] = Field(default=None, max_length=30)


class AccountUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=30)
    role: Optional[Role] = None
    status: Optional[Literal["active", "disabled"]] = None
    user_id: Optional[int] = None


class ApproveBody(BaseModel):
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    user_id: Optional[int] = None  # link to an existing organisation instead of creating one


class RejectBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=300)


class IssueUpdate(BaseModel):
    status: Optional[Literal["open", "in_progress", "resolved", "closed"]] = None
    response: Optional[str] = Field(default=None, max_length=2000)


# ---------------------------------------------------------------- v2.0
class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class MfaLogin(BaseModel):
    challenge: str = Field(min_length=10, max_length=200)
    code: str = Field(min_length=6, max_length=8)


class PolicyBody(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    reference: Optional[str] = Field(default=None, max_length=500)
    class_priority: Optional[Dict[str, int]] = None
    class_min_share: Optional[Dict[str, float]] = None
    essential_classes: Optional[List[str]] = None
    weights: Optional[Dict[str, float]] = None
    guarantee_floor: Optional[float] = None
    lpcd: Optional[float] = None
    zone_min_share: Optional[float] = None
    tanker_trigger_lpcd: Optional[float] = None
    tanker_capacity_litres: Optional[float] = None
    supply_start_hour: Optional[int] = None
    supply_end_hour: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class DecisionBody(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=1000)


class TimetableBody(BaseModel):
    for_date: Optional[date] = None
    demand_mode: Literal["norm", "meter"] = "norm"
    releases: Optional[Dict[int, float]] = None   # {reservoir_id: litres} today's release if different
    note: Optional[str] = Field(default=None, max_length=1000)


class TankerCreate(BaseModel):
    zone_id: int
    litres: float = Field(gt=0, le=1e9)          # total litres of the order
    loads: int = Field(default=1, ge=1, le=10000)
    planned_for: Optional[date] = None
    vehicle: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=500)


class TankerUpdate(BaseModel):
    status: Optional[Literal["planned", "dispatched", "delivered", "cancelled"]] = None
    vehicle: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=500)


class ReservoirCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    capacity_litres: float = Field(gt=0, le=1e12)
    available_litres: float = Field(default=0, ge=0, le=1e12)
    daily_release_litres: Optional[float] = Field(default=None, ge=0, le=1e12)
    pump_capacity_lph: Optional[float] = Field(default=None, gt=0, le=1e12)


class ReservoirNetwork(BaseModel):
    daily_release_litres: Optional[float] = Field(default=None, ge=0, le=1e12)
    pump_capacity_lph: Optional[float] = Field(default=None, gt=0, le=1e12)


class ZoneNetwork(BaseModel):
    reservoir_id: Optional[int] = None
    inlet_capacity_lph: Optional[float] = Field(default=None, gt=0, le=1e12)
    supply_priority: Optional[int] = Field(default=None, ge=1, le=5)
    boundary: Optional[Dict[str, Any]] = None   # GeoJSON Polygon / MultiPolygon (or a Feature)
    clear_boundary: bool = False


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["reservoir_level", "zone_flow"]
    reservoir_id: Optional[int] = None
    zone_id: Optional[int] = None


class DeviceActive(BaseModel):
    active: bool


class Reading(BaseModel):
    value: float
    measured_at: Optional[str] = None


class ReadingsBody(BaseModel):
    readings: List[Reading] = Field(min_length=1, max_length=1000)


class CsvBody(BaseModel):
    csv: str = Field(min_length=1, max_length=5_000_000)


class PrivacyRequestBody(BaseModel):
    kind: Literal["erasure", "correction", "grievance"]
    details: Optional[str] = Field(default=None, max_length=2000)


class PrivacyHandleBody(BaseModel):
    status: Literal["completed", "rejected"]
    response: str = Field(min_length=1, max_length=2000)


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    severity: Literal["low", "medium", "high", "critical"]
    personal_data_affected: bool = False


class IncidentUpdate(BaseModel):
    status: Optional[Literal["open", "contained", "closed"]] = None
    actions: Optional[str] = Field(default=None, max_length=5000)
    reported_cert_in: bool = False
    reported_board: bool = False


class LanguageBody(BaseModel):
    language: Literal["en", "hi"]
