"""设备查询与传感器摘要服务。"""
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Device, Sensor


def build_device_list(
    db: Session,
    *,
    protocol_type: Optional[str] = None,
    online_status: Optional[bool] = None,
    keyword: Optional[str] = None,
    factory: Optional[str] = None,
    workshop: Optional[str] = None,
    production_line: Optional[str] = None,
    device_group: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> dict:
    query = select(Device)
    count_query = select(func.count(Device.id))
    filters = []

    filters.append(Device.deleted_at.is_(None))

    if protocol_type:
        filters.append(Device.protocol_type == protocol_type)
    if online_status is not None:
        filters.append(Device.online_status == online_status)
    if factory:
        filters.append(Device.factory == factory)
    if workshop:
        filters.append(Device.workshop == workshop)
    if production_line:
        filters.append(Device.production_line == production_line)
    if device_group:
        filters.append(Device.device_group == device_group)
    if keyword:
        like = f"%{keyword.strip()}%"
        filters.append(or_(
            Device.device_id.like(like),
            Device.device_name.like(like),
            Device.factory.like(like),
            Device.workshop.like(like),
            Device.production_line.like(like),
            Device.device_group.like(like),
        ))

    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)

    total = db.execute(count_query).scalar() or 0
    devices = db.execute(
        query.order_by(Device.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).scalars().all()

    device_ids = [device.device_id for device in devices]
    sensor_summary = _load_sensor_summary(db, device_ids)

    return {
        "items": [
            _device_to_dict(device, sensor_summary.get(device.device_id, {"count": 0, "preview": ""}))
            for device in devices
        ],
        "total": total,
        "limit": limit,
        "offset": skip
    }


def _load_sensor_summary(db: Session, device_ids: list[str]) -> dict:
    if not device_ids:
        return {}

    sensors = db.execute(
        select(Sensor)
        .where(Sensor.device_id.in_(device_ids))
        .order_by(Sensor.device_id, Sensor.channel_no, Sensor.id)
    ).scalars().all()

    summary = {}
    for sensor in sensors:
        entry = summary.setdefault(sensor.device_id, {"count": 0, "names": []})
        entry["count"] += 1
        if len(entry["names"]) < 3:
            entry["names"].append(sensor.field_cn)

    return {
        device_id: {
            "count": entry["count"],
            "preview": " / ".join(entry["names"])
        }
        for device_id, entry in summary.items()
    }


def _device_to_dict(device: Device, summary: dict) -> dict:
    return {
        "id": device.id,
        "device_id": device.device_id,
        "device_name": device.device_name,
        "protocol_type": device.protocol_type.value if hasattr(device.protocol_type, "value") else device.protocol_type,
        "description": device.description,
        "is_active": device.is_active,
        "is_maintenance": device.is_maintenance,
        "is_test_device": device.is_test_device,
        "offline_threshold": device.offline_threshold,
        "factory": device.factory,
        "workshop": device.workshop,
        "production_line": device.production_line,
        "device_group": device.device_group,
        "online_status": device.online_status,
        "last_active_time": device.last_active_time,
        "device_secret": device.device_secret,
        "gateway_ip": device.gateway_ip,
        "slave_id": device.slave_id,
        "created_at": device.created_at,
        "updated_at": device.updated_at,
        "deleted_at": device.deleted_at,
        "sensor_count": summary["count"],
        "sensor_preview": summary["preview"],
    }
