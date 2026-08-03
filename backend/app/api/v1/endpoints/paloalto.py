"""Palo Alto (PAN-OS) 整合 endpoints（Beta，實驗性，admin only）。

`/test` 回「連線診斷」—— 先換 API key，再逐端點回報通不通與筆數。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import CurrentUser, require_global_read, require_ops_admin
from app.core.audit import append_audit
from app.core.db import get_session
from app.models.paloalto import PaloAltoAddressObject, PaloAltoFirewall, PaloAltoPolicy
from app.schemas.base import Paginated
from app.schemas.paloalto import PaloAltoCreate, PaloAltoRead, PaloAltoUpdate
from app.services import paloalto as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/paloalto", tags=["paloalto"], dependencies=[Depends(require_ops_admin)])
view_router = APIRouter(prefix="/paloalto", tags=["paloalto"],
                        dependencies=[Depends(require_global_read)])


async def _get_or_404(session: AsyncSession, fw_id: uuid.UUID) -> PaloAltoFirewall:
    fw = await session.get(PaloAltoFirewall, fw_id)
    if fw is None:
        raise HTTPException(404, detail="Not found")
    return fw


@view_router.get("", response_model=Paginated[PaloAltoRead])
async def list_firewalls(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[PaloAltoRead]:
    stmt = (select(PaloAltoFirewall).order_by(PaloAltoFirewall.name)
            .offset((page - 1) * page_size).limit(page_size))
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(PaloAltoFirewall)) or 0)
    return Paginated[PaloAltoRead](
        items=[PaloAltoRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=PaloAltoRead, status_code=status.HTTP_201_CREATED)
async def create_firewall(
    payload: PaloAltoCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaloAltoRead:
    data = payload.model_dump(exclude={"password"})
    data["api_url"] = str(data["api_url"]).rstrip("/")
    fw = PaloAltoFirewall(**data, password_enc=b"placeholder", password_nonce=b"placeholder")
    session.add(fw)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(409, detail="Name already exists") from exc
    fw.password_enc, fw.password_nonce = svc.encrypt_password(fw.id, payload.password)
    await session.flush()
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="paloalto_firewall", object_id=str(fw.id), action="create",
        diff={"name": fw.name, "api_url": fw.api_url},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return PaloAltoRead.model_validate(fw)


@router.patch("/{fw_id}", response_model=PaloAltoRead)
async def update_firewall(
    fw_id: uuid.UUID, payload: PaloAltoUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PaloAltoRead:
    fw = await _get_or_404(session, fw_id)
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    for k, v in data.items():
        if k == "api_url" and v is not None:
            v = str(v).rstrip("/")
        setattr(fw, k, v)
    if password:
        fw.password_enc, fw.password_nonce = svc.encrypt_password(fw.id, password)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="paloalto_firewall", object_id=str(fw.id), action="update",
        diff={k: str(v) for k, v in data.items()},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return PaloAltoRead.model_validate(fw)


async def cleanup_shared_rows(session: AsyncSession, fw_id: uuid.UUID) -> None:
    """清掉這台 Palo Alto 寫進共用表的列（`nat_translations` 沒有 cascade）。"""
    from app.models.nat import NATTranslation
    await session.execute(delete(NATTranslation).where(
        NATTranslation.source_origin == f"paloalto:{fw_id}",
    ))


@router.delete("/{fw_id}", status_code=204)
async def delete_firewall(
    fw_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    fw = await _get_or_404(session, fw_id)
    await cleanup_shared_rows(session, fw_id)
    await session.delete(fw)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="paloalto_firewall", object_id=str(fw_id), action="delete", diff={},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/{fw_id}/test")
async def test_firewall(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    fw = await _get_or_404(session, fw_id)
    try:
        return await svc.diagnose(fw)
    except svc.PaloAltoError as exc:
        raise HTTPException(502, detail=str(exc)) from exc


@router.post("/{fw_id}/sync")
async def trigger_sync(
    fw_id: uuid.UUID, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """非同步 —— 立刻回 task_id，同步在背景跑。"""
    fw = await _get_or_404(session, fw_id)
    actor_user_id, fw_name = user.id, fw.name
    actor_ip = request.client.host if request.client else None
    actor_ua = request.headers.get("user-agent")
    request_id = getattr(request.state, "request_id", None)

    async def _runner(sess: AsyncSession, _task: Any) -> dict[str, Any]:
        obj = await sess.get(PaloAltoFirewall, fw_id)
        if obj is None:
            raise RuntimeError("Palo Alto firewall disappeared")
        summary = await svc.sync_instance(sess, obj)
        await append_audit(
            sess, actor_user_id=str(actor_user_id), actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="paloalto_firewall", object_id=str(fw_id), action="sync",
            diff={k: str(v) for k, v in summary.items()}, request_id=request_id,
        )
        await sess.commit()
        return summary

    task = await spawn_task(
        session=session, kind="paloalto.sync", target_type="paloalto_firewall",
        target_id=fw_id, target_label=fw_name, actor_user_id=actor_user_id, runner=_runner,
    )
    return {"task_id": str(task.id), "status": task.status,
            "queued_at": task.queued_at.isoformat()}


# ─────────────────── 唯讀檢視（政策 / 位址物件）───────────────────
@view_router.get("/{fw_id}/policies")
async def list_policies(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    stmt = select(PaloAltoPolicy).where(PaloAltoPolicy.firewall_id == fw_id)
    rows = (await session.execute(stmt.order_by(PaloAltoPolicy.name))).scalars().all()
    return {"items": [{
        "id": str(r.id), "vsys": r.vsys, "name": r.name, "action": r.action,
        "disabled": r.disabled, "from_zone": r.from_zone, "to_zone": r.to_zone,
        "source": r.source, "destination": r.destination,
        "application": r.application, "service": r.service, "description": r.description,
    } for r in rows]}


@view_router.get("/{fw_id}/addresses")
async def list_addresses(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    stmt = select(PaloAltoAddressObject).where(PaloAltoAddressObject.firewall_id == fw_id)
    rows = (await session.execute(stmt.order_by(PaloAltoAddressObject.name))).scalars().all()
    return {"items": [{
        "id": str(r.id), "vsys": r.vsys, "name": r.name, "obj_type": r.obj_type,
        "value": r.value, "description": r.description,
    } for r in rows]}
