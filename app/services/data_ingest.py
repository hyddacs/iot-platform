"""设备数据入库与清洗服务。"""
from datetime import datetime
from numbers import Real
from typing import Dict, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models import Device, DeviceData, Sensor, SensorDataAggHour, SensorDataAggMin, SensorDataPoint
from app.services.alerts import build_threshold_alerts, resolve_recovered_alerts, upsert_active_alerts


def parse_reported_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now()

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.replace(tzinfo=None)
        return parsed
    except Exception:
        return datetime.now()


def load_monitored_sensors(db: Session, device_id: str) -> list[Sensor]:
    return db.execute(
        select(Sensor).where(
            Sensor.device_id == device_id,
            Sensor.is_monitored == True
        )
    ).scalars().all()


def normalize_payload_by_sensors(raw_data: Dict, sensors: Iterable[Sensor]) -> dict:
    sensor_fields = {sensor.field_en for sensor in sensors}
    normalized = {}

    for field, value in raw_data.items():
        if field not in sensor_fields:
            continue
        if isinstance(value, bool) or not isinstance(value, Real):
            raise HTTPException(status_code=400, detail=f"字段 {field} 必须是数值")
        normalized[field] = float(value)

    if not normalized:
        raise HTTPException(status_code=400, detail="上报数据没有匹配的数值型传感器字段")

    return normalized


def floor_to_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def floor_to_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def persist_sensor_points_and_aggregates(
    db: Session,
    *,
    device_id: str,
    device_data_id: int,
    data: dict,
    sensors: Iterable[Sensor],
    reported_at: datetime,
) -> None:
    """写入点位明细，并同步更新分钟/小时聚合。"""
    sensors_by_field = {sensor.field_en: sensor for sensor in sensors}

    for field_en, value in data.items():
        sensor = sensors_by_field.get(field_en)
        if not sensor or not isinstance(value, Real):
            continue

        point = SensorDataPoint(
            device_data_id=device_data_id,
            device_id=device_id,
            sensor_id=sensor.id,
            field_en=field_en,
            value=float(value),
            unit=sensor.unit,
            reported_at=reported_at,
        )
        db.add(point)

        upsert_aggregate(
            db,
            model=SensorDataAggMin,
            device_id=device_id,
            sensor_id=sensor.id,
            field_en=field_en,
            bucket_time=floor_to_minute(reported_at),
            value=float(value),
        )
        upsert_aggregate(
            db,
            model=SensorDataAggHour,
            device_id=device_id,
            sensor_id=sensor.id,
            field_en=field_en,
            bucket_time=floor_to_hour(reported_at),
            value=float(value),
        )


def upsert_aggregate(
    db: Session,
    *,
    model,
    device_id: str,
    sensor_id: int,
    field_en: str,
    bucket_time: datetime,
    value: float,
) -> None:
    """按唯一键(sensor_id, bucket_time)增量更新聚合数据。"""
    table = model.__table__
    insert_stmt = mysql_insert(table).values(
        device_id=device_id,
        sensor_id=sensor_id,
        field_en=field_en,
        bucket_time=bucket_time,
        avg_value=value,
        min_value=value,
        max_value=value,
        first_val=value,
        last_val=value,
        count_value=1,
    )
    update_stmt = insert_stmt.on_duplicate_key_update(
        device_id=insert_stmt.inserted.device_id,
        field_en=insert_stmt.inserted.field_en,
        avg_value=((table.c.avg_value * table.c.count_value) + value) / (table.c.count_value + 1),
        min_value=func.least(table.c.min_value, value),
        max_value=func.greatest(table.c.max_value, value),
        last_val=value,
        count_value=table.c.count_value + 1,
    )
    db.execute(update_stmt)


def persist_device_payload(
    db: Session,
    *,
    device_id: str,
    data: dict,
    reported_at: Optional[datetime] = None,
    sensors: Optional[list[Sensor]] = None,
    suppress_alerts: bool = False
) -> dict:
    now = datetime.now()
    sensors = sensors if sensors is not None else load_monitored_sensors(db, device_id)
    reported_at = reported_at or now

    db.execute(
        update(Device)
        .where(Device.device_id == device_id)
        .values(
            online_status=True,
            last_active_time=now
        )
    )

    device_data = DeviceData(
        device_id=device_id,
        reported_at=reported_at,
        data_json=data
    )
    db.add(device_data)
    db.flush()
    persist_sensor_points_and_aggregates(
        db,
        device_id=device_id,
        device_data_id=device_data.id,
        data=data,
        sensors=sensors,
        reported_at=reported_at,
    )

    changed_alerts = []
    recovered_alerts = []
    if not suppress_alerts:
        alert_payloads, healthy_fields = build_threshold_alerts(device_id, sensors, data)
        changed_alerts = upsert_active_alerts(db, alert_payloads, now=now)
        recovered_alerts = resolve_recovered_alerts(db, device_id, healthy_fields, now=now)
    db.commit()

    return {
        "device_id": device_id,
        "data": data,
        "reported_at": reported_at,
        "alerts": changed_alerts,
        "recovered_alerts": recovered_alerts
    }
