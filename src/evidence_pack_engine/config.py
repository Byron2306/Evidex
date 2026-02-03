from __future__ import annotations

from pydantic import BaseModel, Field


class BillingInfo(BaseModel):
    """Optional billing hints for invoice generation.

    All fields are optional so existing intake.yaml files remain valid.
    """

    client_type: str | None = None  # e.g. "ngo", "corporate", "consultancy"
    quantity: int | None = None
    unit_price: float | None = None
    currency: str | None = None
    service_name: str | None = None


class ClientInfo(BaseModel):
    organization: str
    contact_name: str | None = None
    contact_email: str | None = None


class ReportingPeriod(BaseModel):
    start: str
    end: str


class PackInfo(BaseModel):
    purpose: str
    donor: str | None = None
    project_name: str
    grant_id: str | None = None
    reporting_period: ReportingPeriod
    tone: str = "neutral"
    include_appendix: bool = False


class KPI(BaseModel):
    name: str
    target: float | int | str
    actual: float | int | str
    measurement: str = ""


class Constraints(BaseModel):
    avoid_claims: list[str] = Field(default_factory=list)
    known_gaps: list[str] = Field(default_factory=list)


class Intake(BaseModel):
    client: ClientInfo
    pack: PackInfo
    kpis: list[KPI] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    billing: BillingInfo = Field(default_factory=BillingInfo)
