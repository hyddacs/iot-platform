"""
监控路由
"""
from fastapi import APIRouter, Depends
from app.auth import get_current_user
from app.database import get_db
from app.models import Device, User
from app.sync_modbus_poller import modbus_poller
import psutil
import threading
from sqlalchemy.orm import Session
from sqlalchemy import select

router = APIRouter()


@router.get("/poller")
async def get_poller_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取轮询器状态"""
    poller_status = modbus_poller.get_status()
    gateways = dict(poller_status.get("gateways", {}))

    devices = db.execute(
        select(Device).where(
            Device.protocol_type == "modbus_gateway",
            Device.is_active == True,
            Device.deleted_at.is_(None)
        )
    ).scalars().all()

    for device in devices:
        key = device.gateway_ip or f"test-device:{device.device_id}"
        if key not in gateways:
            gateways[key] = {
                "connected": bool(device.is_test_device),
                "last_checked_at": device.last_active_time.isoformat() if device.last_active_time else None,
                "last_error": "测试设备，使用模拟数据" if device.is_test_device else "尚未完成轮询"
            }

    poller_status["gateways"] = gateways

    return {
        "poller": poller_status,
        "system": {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "thread_count": threading.active_count(),
        }
    }


@router.post("/poller/test-data/{device_id}")
async def insert_modbus_test_data(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """为指定 Modbus 设备插入一条阈值内测试数据。"""
    result = modbus_poller.insert_threshold_safe_test_data(db, device_id)
    return {
        "message": "测试数据插入成功",
        **result
    }
