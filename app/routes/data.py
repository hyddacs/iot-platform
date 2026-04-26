"""
数据查询路由
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, case, func
from datetime import datetime, timedelta
from typing import List, Optional
import logging

from app.database import get_db
from app.auth import get_current_user
from app.models import User, Device, DeviceData, Alert, SensorDataAggHour, SensorDataAggMin, SensorDataPoint
from app.schemas import (
    AlertResolveRequest,
    AlertListResponse,
    AlertResponse,
    AlertSummaryResponse,
    HistoricalAggregateQuery,
    HistoricalAggregateResponse,
    HistoricalDataResponse,
    HistoricalDataQuery,
    HttpDataReport,
    StatsResponse,
)
from app.device_secret_manager import device_secret_manager
from app.services.data_ingest import (
    load_monitored_sensors,
    normalize_payload_by_sensors,
    parse_reported_time,
    persist_device_payload,
)
from app.websocket_manager import websocket_manager
from app.utils.rate_limiter import rate_limit_middleware

logger = logging.getLogger(__name__)
router = APIRouter()


def serialize_alert(
    alert: Alert,
    resolved_by_name: Optional[str] = None,
    device_name: Optional[str] = None,
) -> AlertResponse:
    """序列化告警响应"""
    return AlertResponse(
        id=alert.id,
        device_id=alert.device_id,
        device_name=device_name,
        sensor_field_en=alert.sensor_field_en,
        sensor_field_cn=alert.sensor_field_cn,
        current_value=alert.current_value,
        threshold_value=alert.threshold_value,
        alert_type=alert.alert_type.value if hasattr(alert.alert_type, "value") else str(alert.alert_type),
        alert_level=alert.alert_level.value if hasattr(alert.alert_level, "value") else str(alert.alert_level),
        status=alert.status or ("resolved" if alert.is_resolved else "active"),
        first_triggered_at=alert.first_triggered_at or alert.created_at,
        last_triggered_at=alert.last_triggered_at or alert.updated_at or alert.created_at,
        occurrence_count=alert.occurrence_count or 1,
        recovered_at=alert.recovered_at,
        is_resolved=alert.is_resolved,
        resolved_at=alert.resolved_at,
        resolved_by=alert.resolved_by,
        resolved_by_name=resolved_by_name,
        resolve_note=alert.resolve_note,
        created_at=alert.created_at,
    )


def build_alert_conditions(
    *,
    device_id: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    alert_status: Optional[str] = None,
    alert_type: Optional[str] = None,
):
    conditions = []

    if device_id:
        conditions.append(Alert.device_id == device_id)
    if is_resolved is not None:
        conditions.append(Alert.is_resolved == is_resolved)
    if alert_status:
        conditions.append(Alert.status == alert_status)
    if alert_type:
        conditions.append(Alert.alert_type == alert_type)

    return conditions


def load_alert_page(
    db: Session,
    *,
    device_id: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    alert_status: Optional[str] = None,
    alert_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    conditions = build_alert_conditions(
        device_id=device_id,
        is_resolved=is_resolved,
        alert_status=alert_status,
        alert_type=alert_type,
    )

    alert_query = select(Alert)
    count_query = select(func.count(Alert.id))
    if conditions:
        alert_query = alert_query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    total = db.execute(count_query).scalar() or 0
    alerts = db.execute(
        alert_query.order_by(
            func.coalesce(Alert.last_triggered_at, Alert.created_at).desc(),
            Alert.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    user_ids = {alert.resolved_by for alert in alerts if alert.resolved_by is not None}
    device_ids = {alert.device_id for alert in alerts}
    user_map = {}
    if user_ids:
        users = db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
        user_map = {user.id: user.username for user in users}
    device_map = {}
    if device_ids:
        devices = db.execute(
            select(Device.device_id, Device.device_name).where(Device.device_id.in_(device_ids))
        ).all()
        device_map = {device_id: device_name for device_id, device_name in devices}

    return {
        "items": [
            serialize_alert(
                alert,
                user_map.get(alert.resolved_by),
                device_map.get(alert.device_id),
            )
            for alert in alerts
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/report/http")
async def report_http_device_data(
    report: HttpDataReport,  # 修改模型名称
    db: Session = Depends(get_db),
):
    """HTTP主动上报设备数据上报"""
    # 限流保护
    rate_limit_middleware(
        client_key=report.device_id,
        max_requests=100,
        time_window=60
    )
    
    # 验证设备
    is_valid = device_secret_manager.verify_device(
        report.device_id, report.device_secret
    )
    
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="设备认证失败：无效的设备ID或密钥"
        )

    device = db.execute(
        select(Device).where(Device.device_id == report.device_id, Device.deleted_at.is_(None))
    ).scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="设备不存在或已归档")
    if not device.is_active:
        raise HTTPException(status_code=400, detail="设备已停用，拒绝接收上报数据")
    
    sensors = load_monitored_sensors(db, report.device_id)
    normalized_data = normalize_payload_by_sensors(report.data, sensors)
    result = persist_device_payload(
        db,
        device_id=report.device_id,
        data=normalized_data,
        reported_at=parse_reported_time(report.timestamp),
        sensors=sensors,
        suppress_alerts=device.is_maintenance,
    )

    for alert in result["alerts"]:
        await websocket_manager.broadcast_alert(alert)
    for alert in result["recovered_alerts"]:
        await websocket_manager.broadcast_alert(alert)
    await websocket_manager.broadcast_device_data(report.device_id, normalized_data)

    return {
        "status": "success",
        "message": "数据已验证并入库",
        "timestamp": datetime.now().isoformat(),
        "accepted_fields": list(normalized_data.keys()),
        "alert_count": len(result["alerts"]),
        "recovered_alert_count": len(result["recovered_alerts"]),
        "maintenance_mode": device.is_maintenance,
    }


def resolve_aggregate_model(query: HistoricalAggregateQuery):
    if query.bucket == "minute":
        return "minute", SensorDataAggMin
    if query.bucket == "hour":
        return "hour", SensorDataAggHour

    if query.start_time and query.end_time:
        span = query.end_time - query.start_time
        if span >= timedelta(days=3):
            return "hour", SensorDataAggHour

    return "minute", SensorDataAggMin


@router.get("/historical/aggregated", response_model=HistoricalAggregateResponse)
async def get_historical_aggregated_data(
    query: HistoricalAggregateQuery = Depends(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """读取分钟/小时聚合序列，供趋势图和大范围历史分析使用。"""
    device = db.execute(
        select(Device).where(Device.device_id == query.device_id, Device.deleted_at.is_(None))
    ).scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    bucket_name, aggregate_model = resolve_aggregate_model(query)
    conditions = [aggregate_model.device_id == query.device_id]
    if query.field_en:
        conditions.append(aggregate_model.field_en == query.field_en)
    if query.start_time:
        conditions.append(aggregate_model.bucket_time >= query.start_time)
    if query.end_time:
        conditions.append(aggregate_model.bucket_time <= query.end_time)

    total = 0
    if query.include_total:
        total = db.execute(
            select(func.count(func.distinct(aggregate_model.bucket_time)))
            .where(and_(*conditions))
        ).scalar() or 0

    bucket_times = [
        row.bucket_time for row in db.execute(
            select(aggregate_model.bucket_time)
            .where(and_(*conditions))
            .group_by(aggregate_model.bucket_time)
            .order_by(aggregate_model.bucket_time.desc())
            .limit(query.limit)
        ).all()
    ]

    if not bucket_times:
        return {
            "items": [],
            "total": total,
            "limit": query.limit,
            "bucket": bucket_name,
        }

    rows = db.execute(
        select(aggregate_model)
        .where(
            aggregate_model.device_id == query.device_id,
            aggregate_model.bucket_time.in_(bucket_times),
            *((aggregate_model.field_en == query.field_en,) if query.field_en else ()),
        )
        .order_by(aggregate_model.bucket_time.desc(), aggregate_model.field_en)
    ).scalars().all()

    buckets = {
        bucket_time: {
            "id": f"{bucket_name}-{bucket_time.isoformat()}",
            "device_id": query.device_id,
            "bucket_time": bucket_time,
            "data": {},
            "min_data": {},
            "max_data": {},
            "count_data": {},
            "reported_at": bucket_time,
            "created_at": bucket_time,
            "source": bucket_name,
        }
        for bucket_time in bucket_times
    }

    for row in rows:
        if row.avg_value is None:
            continue
        bucket = buckets[row.bucket_time]
        bucket["data"][row.field_en] = float(row.avg_value)
        if row.min_value is not None:
            bucket["min_data"][row.field_en] = float(row.min_value)
        if row.max_value is not None:
            bucket["max_data"][row.field_en] = float(row.max_value)
        bucket["count_data"][row.field_en] = int(row.count_value or 0)

    return {
        "items": [buckets[bucket_time] for bucket_time in bucket_times],
        "total": total,
        "limit": query.limit,
        "bucket": bucket_name,
    }


@router.get("/historical", response_model=HistoricalDataResponse)
async def get_historical_data(
    query: HistoricalDataQuery = Depends(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """查询历史数据"""
    conditions = [DeviceData.device_id == query.device_id]
    
    if query.start_time:
        conditions.append(DeviceData.reported_at >= query.start_time)
    if query.end_time:
        conditions.append(DeviceData.reported_at <= query.end_time)
    
    total = 0
    if query.include_total:
        total = db.execute(
            select(func.count(DeviceData.id))
            .where(and_(*conditions))
        ).scalar() or 0

    result = db.execute(
        select(DeviceData)
        .where(and_(*conditions))
        .order_by(DeviceData.reported_at.desc(), DeviceData.id.desc())
        .offset(query.offset)
        .limit(query.limit)
    )
    
    records = result.scalars().all()
    record_ids = [record.id for record in records]
    point_rows = []
    if record_ids:
        point_rows = db.execute(
            select(SensorDataPoint)
            .where(SensorDataPoint.device_data_id.in_(record_ids))
            .order_by(SensorDataPoint.device_data_id, SensorDataPoint.field_en)
        ).scalars().all()

    points_by_record_id = {}
    for point in point_rows:
        points_by_record_id.setdefault(point.device_data_id, {})[point.field_en] = point.value
    
    return {
        "items": [
            {
                "id": record.id,
                "device_id": record.device_id,
                "data": points_by_record_id.get(record.id) or record.data_json,
                "reported_at": record.reported_at,
                "created_at": record.created_at
            }
            for record in records
        ],
        "total": total,
        "limit": query.limit,
        "offset": query.offset
    }


@router.get("/latest/{device_id}")
async def get_latest_data(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取设备最新数据"""
    result = db.execute(select(Device).where(Device.device_id == device_id, Device.deleted_at.is_(None)))
    device = result.scalar_one_or_none()
    
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    
    result = db.execute(
        select(DeviceData)
        .where(DeviceData.device_id == device_id)
        .order_by(func.coalesce(DeviceData.reported_at, DeviceData.created_at).desc())
        .limit(1)
    )
    latest_data = result.scalar_one_or_none()
    
    return {
        "device_id": device_id,
        "device_name": device.device_name,
        "online_status": device.online_status,
        "last_active_time": device.last_active_time.isoformat() if device.last_active_time else None,
        "latest_data": latest_data.data_json if latest_data else None,
        "data_time": latest_data.reported_at.isoformat() if latest_data and latest_data.reported_at else (
            latest_data.created_at.isoformat() if latest_data else None
        )
    }


@router.get("/latest")
async def get_latest_data_batch(
    limit: int = 12,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """批量获取设备最新数据快照。"""
    devices = db.execute(
        select(Device)
        .where(Device.deleted_at.is_(None))
        .order_by(Device.created_at.desc())
        .limit(limit)
    ).scalars().all()

    device_ids = [device.device_id for device in devices]
    latest_by_device_id = {}
    if device_ids:
        ranked_latest = (
            select(
                DeviceData.device_id.label("device_id"),
                DeviceData.data_json.label("data_json"),
                DeviceData.reported_at.label("reported_at"),
                func.row_number()
                .over(
                    partition_by=DeviceData.device_id,
                    order_by=(DeviceData.reported_at.desc(), DeviceData.id.desc()),
                )
                .label("row_number"),
            )
            .where(DeviceData.device_id.in_(device_ids))
            .subquery()
        )
        latest_rows = db.execute(
            select(
                ranked_latest.c.device_id,
                ranked_latest.c.data_json,
                ranked_latest.c.reported_at,
            )
            .where(ranked_latest.c.row_number == 1)
        ).all()
        latest_by_device_id = {row.device_id: row for row in latest_rows}

    snapshots = []
    for device in devices:
        latest_data = latest_by_device_id.get(device.device_id)

        snapshots.append({
            "device_id": device.device_id,
            "device_name": device.device_name,
            "protocol_type": device.protocol_type.value if hasattr(device.protocol_type, "value") else device.protocol_type,
            "online_status": device.online_status,
            "last_active_time": device.last_active_time.isoformat() if device.last_active_time else None,
            "latest_data": latest_data.data_json if latest_data else None,
            "data_time": latest_data.reported_at.isoformat() if latest_data and latest_data.reported_at else None
        })

    return snapshots


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取平台统计信息"""
    device_stats = db.execute(
        select(
            func.count(Device.id).label("total_devices"),
            func.sum(case((Device.online_status == True, 1), else_=0)).label("online_devices"),
            func.sum(case((Device.protocol_type == "modbus_gateway", 1), else_=0)).label("modbus_devices"),
            func.sum(case((Device.protocol_type == "http_active", 1), else_=0)).label("http_devices"),
        )
        .where(Device.deleted_at.is_(None))
    ).one()

    total_devices = int(device_stats.total_devices or 0)
    online_devices = int(device_stats.online_devices or 0)
    modbus_devices = int(device_stats.modbus_devices or 0)
    http_devices = int(device_stats.http_devices or 0)
    alert_stats = db.execute(
        select(
            func.count(Alert.id).label("total_alerts_unresolved"),
            func.sum(case((Alert.alert_type == "UPPER_LIMIT", 1), else_=0)).label("upper_alerts_unresolved"),
            func.sum(case((Alert.alert_type == "LOWER_LIMIT", 1), else_=0)).label("lower_alerts_unresolved"),
            func.count(func.distinct(Alert.device_id)).label("alert_devices_unresolved"),
            func.count(func.distinct(Alert.sensor_field_en)).label("alert_fields_unresolved"),
        )
        .where(Alert.is_resolved == False)
    ).one()
    total_alerts_unresolved = int(alert_stats.total_alerts_unresolved or 0)
    
    return StatsResponse(
        total_devices=total_devices,
        online_devices=online_devices,
        offline_devices=total_devices - online_devices,
        modbus_devices=modbus_devices,
        http_devices=http_devices,  # 修改字段名
        total_alerts_unresolved=total_alerts_unresolved,
        upper_alerts_unresolved=int(alert_stats.upper_alerts_unresolved or 0),
        lower_alerts_unresolved=int(alert_stats.lower_alerts_unresolved or 0),
        alert_devices_unresolved=int(alert_stats.alert_devices_unresolved or 0),
        alert_fields_unresolved=int(alert_stats.alert_fields_unresolved or 0),
    )


@router.get("/alerts/page", response_model=AlertListResponse)
async def get_alerts_page(
    device_id: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    alert_status: Optional[str] = None,
    alert_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """分页查询告警列表，返回 total。"""
    return load_alert_page(
        db,
        device_id=device_id,
        is_resolved=is_resolved,
        alert_status=alert_status,
        alert_type=alert_type,
        limit=limit,
        offset=offset,
    )


@router.get("/alerts/summary", response_model=AlertSummaryResponse)
async def get_alerts_summary(
    device_id: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    alert_status: Optional[str] = None,
    alert_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """查询告警聚合统计，避免前端分页后只统计当前页。"""
    conditions = build_alert_conditions(
        device_id=device_id,
        is_resolved=is_resolved,
        alert_status=alert_status,
        alert_type=alert_type,
    )
    summary_query = select(
        func.count(Alert.id).label("total"),
        func.sum(case((Alert.is_resolved == False, 1), else_=0)).label("unresolved"),
        func.sum(case((Alert.is_resolved == True, 1), else_=0)).label("resolved"),
        func.sum(case((Alert.alert_type == "UPPER_LIMIT", 1), else_=0)).label("upper"),
        func.sum(case((Alert.alert_type == "LOWER_LIMIT", 1), else_=0)).label("lower"),
        func.sum(case((Alert.status == "recovered", 1), else_=0)).label("recovered"),
        func.count(func.distinct(Alert.device_id)).label("affected_devices"),
    )
    if conditions:
        summary_query = summary_query.where(and_(*conditions))

    summary = db.execute(summary_query).one()
    return AlertSummaryResponse(
        total=int(summary.total or 0),
        unresolved=int(summary.unresolved or 0),
        resolved=int(summary.resolved or 0),
        upper=int(summary.upper or 0),
        lower=int(summary.lower or 0),
        recovered=int(summary.recovered or 0),
        affected_devices=int(summary.affected_devices or 0),
    )


@router.get("/alerts", response_model=List[AlertResponse])
async def get_alerts(
    device_id: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    alert_status: Optional[str] = None,
    alert_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """查询告警列表，保留旧数组响应兼容现有调用。"""
    page = load_alert_page(
        db,
        device_id=device_id,
        is_resolved=is_resolved,
        alert_status=alert_status,
        alert_type=alert_type,
        limit=limit,
        offset=offset,
    )
    return page["items"]


@router.post("/alerts/{alert_id}/resolve", response_model=AlertResponse)
async def resolve_alert(
    alert_id: int,
    payload: AlertResolveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """处理告警"""
    result = db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()

    if not alert:
        raise HTTPException(status_code=404, detail="告警不存在")

    alert.is_resolved = True
    alert.status = "resolved"
    alert.resolved_at = datetime.now()
    alert.resolved_by = current_user.id
    alert.resolve_note = payload.resolve_note

    db.commit()
    db.refresh(alert)
    return serialize_alert(alert, current_user.username)
