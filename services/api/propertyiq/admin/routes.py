"""Admin API. Every route except the lockdown banner status needs the 'admin' role."""
from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from propertyiq.admin import checklist, lockdown


class LockdownRequest(BaseModel):
    mode: Literal["app", "computer", "off"]


def build_router(user_dependency: Callable) -> APIRouter:
    router = APIRouter()

    def admin_user(user=Depends(user_dependency)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin only")
        return user

    @router.get("/api/lockdown/status")
    def lockdown_banner(_=Depends(user_dependency)) -> dict:
        """For the header banner; any signed-in user. Reads a local state file, so it's instant."""
        return lockdown.quick_mode()

    @router.get("/api/admin/checklist")
    def security_checklist(request: Request, user=Depends(admin_user)) -> dict:
        lockdown.log_action(user.username, "viewed security checklist")
        return checklist.build(request.app)

    @router.get("/api/admin/lockdown")
    def lockdown_status(_=Depends(admin_user)) -> dict:
        return {**lockdown.firewall_status(), "api_can_reach_internet": lockdown.can_reach_internet()}

    @router.post("/api/admin/lockdown", status_code=202)
    def change_lockdown(body: LockdownRequest, user=Depends(admin_user)) -> dict:
        lockdown.request_change(body.mode, user.username)
        return {"requested": body.mode,
                "message": "Windows is asking for permission on this PC's screen. Click Yes there to apply it."}

    return router
