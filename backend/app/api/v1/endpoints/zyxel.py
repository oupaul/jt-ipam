"""Zyxel 防火牆整合 endpoints（Beta，實驗性，admin only）。

`/test` 回「連線診斷」—— 逐指令回報通不通與抓到的原始輸出片段，方便無實機開發後
上線時快速核對實際 CLI 輸出格式跟解析器是否對得上。
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
from app.models.zyxel import ZyxelAddressObject, ZyxelFirewall, ZyxelPolicy
from app.schemas.base import Paginated
from app.schemas.zyxel import ZyxelCreate, ZyxelRead, ZyxelUpdate
from app.services import zyxel as svc
from app.services.background_tasks import spawn_task

router = APIRouter(prefix="/zyxel", tags=["zyxel"], dependencies=[Depends(require_ops_admin)])
# 政策 / 位址物件屬「全域基礎設施資料」→ 唯讀檢視給具全域讀取權者（比照 FortiGate）
view_router = APIRouter(prefix="/zyxel", tags=["zyxel"], dependencies=[Depends(require_global_read)])


async def _get_or_404(session: AsyncSession, fw_id: uuid.UUID) -> ZyxelFirewall:
    fw = await session.get(ZyxelFirewall, fw_id)
    if fw is None:
        raise HTTPException(404, detail="Not found")
    return fw


@view_router.get("", response_model=Paginated[ZyxelRead])
async def list_firewalls(
    session: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(50, ge=1, le=500),
) -> Paginated[ZyxelRead]:
    stmt = (select(ZyxelFirewall).order_by(ZyxelFirewall.name)
            .offset((page - 1) * page_size).limit(page_size))
    rows = list((await session.execute(stmt)).scalars().all())
    total = int(await session.scalar(select(func.count()).select_from(ZyxelFirewall)) or 0)
    return Paginated[ZyxelRead](
        items=[ZyxelRead.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=ZyxelRead, status_code=status.HTTP_201_CREATED)
async def create_firewall(
    payload: ZyxelCreate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ZyxelRead:
    data = payload.model_dump(exclude={"password"})
    fw = ZyxelFirewall(**data, password_enc=b"placeholder", password_nonce=b"placeholder")
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
        object_type="zyxel_firewall", object_id=str(fw.id), action="create",
        diff={"name": fw.name, "host": fw.host},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return ZyxelRead.model_validate(fw)


@router.patch("/{fw_id}", response_model=ZyxelRead)
async def update_firewall(
    fw_id: uuid.UUID, payload: ZyxelUpdate, user: CurrentUser, request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ZyxelRead:
    fw = await _get_or_404(session, fw_id)
    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    for k, v in data.items():
        setattr(fw, k, v)
    if password:
        fw.password_enc, fw.password_nonce = svc.encrypt_password(fw.id, password)
    await append_audit(
        session, actor_user_id=str(user.id),
        actor_ip=request.client.host if request.client else None,
        actor_user_agent=request.headers.get("user-agent"),
        object_type="zyxel_firewall", object_id=str(fw.id), action="update",
        diff={k: str(v) for k, v in data.items()},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await session.refresh(fw)
    return ZyxelRead.model_validate(fw)


async def cleanup_shared_rows(session: AsyncSession, fw_id: uuid.UUID) -> None:
    """清掉這台 Zyxel 寫進共用表的列（`nat_translations` 沒有 cascade，只能清自己的列）。"""
    from app.models.nat import NATTranslation
    await session.execute(delete(NATTranslation).where(
        NATTranslation.source_origin == f"zyxel:{fw_id}",
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
        object_type="zyxel_firewall", object_id=str(fw_id), action="delete", diff={},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()


@router.post("/{fw_id}/test")
async def test_firewall(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """連線診斷：逐指令回報是否可執行、原始輸出片段。"""
    fw = await _get_or_404(session, fw_id)
    try:
        return await svc.diagnose(fw)
    except svc.ZyxelError as exc:
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
        obj = await sess.get(ZyxelFirewall, fw_id)
        if obj is None:
            raise RuntimeError("Zyxel firewall disappeared")
        summary = await svc.sync_instance(sess, obj)
        await append_audit(
            sess, actor_user_id=str(actor_user_id), actor_ip=actor_ip, actor_user_agent=actor_ua,
            object_type="zyxel_firewall", object_id=str(fw_id), action="sync",
            diff={k: str(v) for k, v in summary.items()}, request_id=request_id,
        )
        await sess.commit()
        return summary

    task = await spawn_task(
        session=session, kind="zyxel.sync", target_type="zyxel_firewall",
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
    stmt = select(ZyxelPolicy).where(ZyxelPolicy.firewall_id == fw_id)
    rows = (await session.execute(stmt.order_by(ZyxelPolicy.rule_number))).scalars().all()
    return {"items": [{
        "id": str(r.id), "rule_number": r.rule_number, "name": r.name,
        "status": r.status, "action": r.action, "from_zone": r.from_zone, "to_zone": r.to_zone,
        "source": r.source, "destination": r.destination, "service": r.service,
        "description": r.description,
    } for r in rows]}


@view_router.get("/{fw_id}/addresses")
async def list_addresses(
    fw_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    stmt = select(ZyxelAddressObject).where(ZyxelAddressObject.firewall_id == fw_id)
    rows = (await session.execute(stmt.order_by(ZyxelAddressObject.name))).scalars().all()
    return {"items": [{
        "id": str(r.id), "name": r.name, "obj_type": r.obj_type, "value": r.value,
    } for r in rows]}
