"""告警生命周期服务。"""
from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models import Alert


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def upsert_active_alerts(
    db: Session,
    alert_payloads: Iterable[dict],
    now: Optional[datetime] = None
) -> List[dict]:
    """同一设备/字段/类型未处理告警只保留一条，避免持续超限刷屏。"""
    now = now or datetime.now()
    alert_payloads = list(alert_payloads)
    if not alert_payloads:
        return []

    changed_alerts = []
    alert_keys = {
        (payload["device_id"], payload["sensor_field_en"], payload["alert_type"])
        for payload in alert_payloads
    }
    existing_alerts = db.execute(
        select(Alert).where(
            tuple_(Alert.device_id, Alert.sensor_field_en, Alert.alert_type).in_(alert_keys),
            Alert.status == "active",
            Alert.is_resolved == False
        )
    ).scalars().all()
    existing_by_key = {
        (
            alert.device_id,
            alert.sensor_field_en,
            _enum_value(alert.alert_type),
        ): alert
        for alert in existing_alerts
    }

    for payload in alert_payloads:
        existing = existing_by_key.get((
            payload["device_id"],
            payload["sensor_field_en"],
            payload["alert_type"],
        ))

        if existing:
            if existing.first_triggered_at is None:
                existing.first_triggered_at = existing.created_at or now
            existing.current_value = payload["current_value"]
            existing.threshold_value = payload["threshold_value"]
            existing.alert_level = payload.get("alert_level", existing.alert_level)
            existing.last_triggered_at = now
            existing.occurrence_count = (existing.occurrence_count or 1) + 1
            existing.status = "active"
            existing.updated_at = now
            changed_alerts.append(_serialize_alert_payload(existing))
        else:
            alert = Alert(
                **payload,
                status="active",
                first_triggered_at=now,
                last_triggered_at=now,
                occurrence_count=1
            )
            db.add(alert)
            db.flush()
            changed_alerts.append(_serialize_alert_payload(alert))

    return changed_alerts


def resolve_recovered_alerts(
    db: Session,
    device_id: str,
    healthy_fields: Iterable[str],
    now: Optional[datetime] = None
) -> List[dict]:
    """传感器恢复到阈值内时把告警标记为 recovered，等待人工归档。"""
    healthy_fields = set(healthy_fields)
    if not healthy_fields:
        return []

    now = now or datetime.now()
    alerts = db.execute(
        select(Alert).where(
            Alert.device_id == device_id,
            Alert.sensor_field_en.in_(healthy_fields),
            Alert.status == "active",
            Alert.is_resolved == False
        )
    ).scalars().all()

    resolved = []
    for alert in alerts:
        alert.status = "recovered"
        alert.recovered_at = now
        alert.resolve_note = "数据恢复正常，等待人工确认"
        resolved.append(_serialize_alert_payload(alert))

    return resolved


def build_threshold_alerts(device_id: str, sensors: Iterable, data: dict) -> tuple[List[dict], List[str]]:
    """基于传感器配置生成告警，并返回已恢复正常的字段。"""
    alert_payloads = []
    healthy_fields = []

    for sensor in sensors:
        if sensor.field_en not in data:
            continue

        value = data[sensor.field_en]
        current_alert = None

        if sensor.lower_threshold is not None and value < sensor.lower_threshold:
            current_alert = {
                "device_id": device_id,
                "sensor_field_en": sensor.field_en,
                "sensor_field_cn": sensor.field_cn,
                "current_value": value,
                "threshold_value": sensor.lower_threshold,
                "alert_type": "LOWER_LIMIT",
                "alert_level": "WARNING"
            }
        elif sensor.upper_threshold is not None and value > sensor.upper_threshold:
            current_alert = {
                "device_id": device_id,
                "sensor_field_en": sensor.field_en,
                "sensor_field_cn": sensor.field_cn,
                "current_value": value,
                "threshold_value": sensor.upper_threshold,
                "alert_type": "UPPER_LIMIT",
                "alert_level": "WARNING"
            }

        if current_alert:
            alert_payloads.append(current_alert)
        else:
            healthy_fields.append(sensor.field_en)

    return alert_payloads, healthy_fields


def _serialize_alert_payload(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "device_id": alert.device_id,
        "sensor_field_en": alert.sensor_field_en,
        "sensor_field_cn": alert.sensor_field_cn,
        "current_value": alert.current_value,
        "threshold_value": alert.threshold_value,
        "alert_type": _enum_value(alert.alert_type),
        "alert_level": _enum_value(alert.alert_level),
        "status": alert.status or ("resolved" if alert.is_resolved else "active"),
        "first_triggered_at": alert.first_triggered_at.isoformat() if alert.first_triggered_at else None,
        "last_triggered_at": alert.last_triggered_at.isoformat() if alert.last_triggered_at else None,
        "occurrence_count": alert.occurrence_count or 1,
        "recovered_at": alert.recovered_at.isoformat() if alert.recovered_at else None,
        "is_resolved": alert.is_resolved,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolve_note": alert.resolve_note,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }
