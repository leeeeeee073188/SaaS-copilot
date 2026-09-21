"""Demo-only routes; the service repeats authorization inside every operation."""
import os
import secrets
from dataclasses import asdict
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from saas.service import BusinessError


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    user_id: str
    org_id: str


class PlanChange(Input):
    target_plan: str


class ApplyChange(Input):
    operation_id: str


class Invite(Input):
    email: str
    idempotency_key: str


class Clock(Input):
    timestamp: str


def build_router(service):
    router = APIRouter(prefix="/saas", tags=["FlowForge sandbox"])

    def invoke(fn, *args):
        try:
            return fn(*args)
        except BusinessError as ex:
            raise HTTPException(ex.status, {"code": ex.code, "message": str(ex)}) from ex

    def actor(authorization: str = Header(default="")):
        return invoke(service.actor, authorization.removeprefix("Bearer "))

    @router.post("/demo/login")
    def login(body: Login):
        return invoke(service.login, body.user_id, body.org_id)

    @router.get("/session")
    def session(who=Depends(actor)):
        return asdict(who)

    @router.get("/plans")
    def plans(who=Depends(actor)):
        return invoke(service.read, who, "plans")

    @router.get("/me/{resource}")
    def read(resource: str, who=Depends(actor)):
        return invoke(service.read, who, resource)

    @router.get("/integrations/{integration_id}/requests")
    def requests(integration_id: str, who=Depends(actor)):
        return invoke(service.read, who, "requests", integration_id)

    @router.post("/subscription/change-previews")
    def preview(body: PlanChange, who=Depends(actor)):
        return invoke(service.preview, who, body.target_plan)

    @router.post("/operations/{op}/confirm")
    def confirm(op: str, who=Depends(actor)):
        return invoke(service.confirm, who, op)

    @router.post("/subscription/changes")
    def apply(body: ApplyChange, who=Depends(actor)):
        return invoke(service.apply, who, body.operation_id)

    @router.post("/invitations")
    def invite(body: Invite, who=Depends(actor)):
        return invoke(service.invite, who, body.email, body.idempotency_key)

    @router.get("/operations/{op}")
    def operation(op: str, who=Depends(actor)):
        return invoke(service.operation, who, op)

    @router.post("/demo/advance-clock")
    def advance(body: Clock, x_demo_control: str = Header(default="")):
        expected = os.getenv("SAAS_COPILOT_DEMO_CONTROL_KEY", "")
        if not expected or not secrets.compare_digest(expected, x_demo_control):
            raise HTTPException(403, "Demo control credential required")
        return invoke(service.advance_clock, body.timestamp)

    return router
