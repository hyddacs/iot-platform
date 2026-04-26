"""
设备管理路由
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import delete, select
from typing import List, Optional
import logging
from datetime import datetime

from app.database import get_db
from app.auth import get_current_user
from app.models import Device, Sensor, User
from app.schemas import (
    DeviceCreateWithSensors,
    DeviceListResponse,
    DeviceResponse,
    DeviceUpdate,
    SensorCreate,
    SensorResponse,
    SensorUpdate,
)
from app.device_secret_manager import device_secret_manager
from app.services.devices import build_device_list
from app.utils.security import generate_random_secret

logger = logging.getLogger(__name__)
router = APIRouter()


def validate_sensor_constraints(
    *,
    db: Session,
    device_id: str,
    field_en: str,
    channel_no: Optional[int],
    lower_threshold: Optional[float],
    upper_threshold: Optional[float],
    current_sensor_id: Optional[int] = None
):
    """在落库前同步数据库约束，返回友好的 400 错误。"""
    if lower_threshold is not None and upper_threshold is not None and lower_threshold > upper_threshold:
        raise HTTPException(status_code=400, detail="下限阈值不能大于上限阈值")

    duplicate_field_query = select(Sensor).where(
        Sensor.device_id == device_id,
        Sensor.field_en == field_en
    )
    if current_sensor_id is not None:
        duplicate_field_query = duplicate_field_query.where(Sensor.id != current_sensor_id)
    if db.execute(duplicate_field_query).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="传感器字段英文名已存在")

    if channel_no is not None:
        duplicate_channel_query = select(Sensor).where(
            Sensor.device_id == device_id,
            Sensor.channel_no == channel_no
        )
        if current_sensor_id is not None:
            duplicate_channel_query = duplicate_channel_query.where(Sensor.id != current_sensor_id)
        if db.execute(duplicate_channel_query).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="传感器通道号已存在")


def validate_new_device_sensors(sensors: List[SensorCreate]):
    """校验创建设备时随附的传感器列表，避免部分成功。"""
    seen_fields = set()
    seen_channels = set()

    for sensor in sensors:
        if sensor.field_en in seen_fields:
            raise HTTPException(status_code=400, detail=f"传感器字段英文名重复: {sensor.field_en}")
        seen_fields.add(sensor.field_en)

        if sensor.channel_no is not None:
            if sensor.channel_no in seen_channels:
                raise HTTPException(status_code=400, detail=f"传感器通道号重复: {sensor.channel_no}")
            seen_channels.add(sensor.channel_no)


@router.post("", response_model=DeviceResponse)
async def create_device(
    device_data: DeviceCreateWithSensors,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建设备（统一接口）"""
    result = db.execute(select(Device).where(Device.device_id == device_data.device_id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="设备ID已存在")
    
    validate_new_device_sensors(device_data.sensors)

    device_dict = device_data.dict(exclude_unset=True)
    sensors_data = device_dict.pop("sensors", [])
    
    if device_data.protocol_type == "http_active":
        if not device_dict.get("device_secret"):
            device_dict["device_secret"] = generate_random_secret(16)
    else:
        device_dict.pop("device_secret", None)
        if device_dict.get("is_test_device"):
            device_dict["gateway_ip"] = device_dict.get("gateway_ip") or "127.0.0.1"
            device_dict["slave_id"] = device_dict.get("slave_id") or 1
    
    device = Device(**device_dict)
    db.add(device)
    
    for sensor_payload in sensors_data:
        sensor = Sensor(
            device_id=device.device_id,
            **sensor_payload
        )
        db.add(sensor)

    db.commit()
    db.refresh(device)
    
    if device.protocol_type == "http_active":
        device_secret_manager.update_device(
            device.device_id, 
            device.device_secret, 
            device.protocol_type
        )
    
    logger.info(f"创建设备成功: {device.device_id}")
    return device


@router.get("", response_model=DeviceListResponse)
async def get_devices(
    protocol_type: Optional[str] = None,
    online_status: Optional[bool] = None,
    keyword: Optional[str] = None,
    factory: Optional[str] = None,
    workshop: Optional[str] = None,
    production_line: Optional[str] = None,
    device_group: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取设备列表"""
    return build_device_list(
        db,
        protocol_type=protocol_type,
        online_status=online_status,
        keyword=keyword,
        factory=factory,
        workshop=workshop,
        production_line=production_line,
        device_group=device_group,
        skip=skip,
        limit=limit,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取设备详情"""
    result = db.execute(select(Device).where(Device.device_id == device_id, Device.deleted_at.is_(None)))
    device = result.scalar_one_or_none()
    
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    
    return device


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: str,
    device_data: DeviceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新设备"""
    result = db.execute(select(Device).where(Device.device_id == device_id, Device.deleted_at.is_(None)))
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    update_data = device_data.dict(exclude_unset=True)

    next_protocol = update_data.get("protocol_type", device.protocol_type)
    next_is_test_device = update_data.get("is_test_device", device.is_test_device)
    next_gateway_ip = update_data.get("gateway_ip", device.gateway_ip)
    next_slave_id = update_data.get("slave_id", device.slave_id)
    next_device_secret = update_data.get("device_secret", device.device_secret)

    if next_protocol == "http_active":
        update_data["gateway_ip"] = None
        update_data["slave_id"] = None
        if not next_device_secret:
            update_data["device_secret"] = generate_random_secret(16)
    elif next_protocol == "modbus_gateway":
        if next_is_test_device:
            next_gateway_ip = next_gateway_ip or "127.0.0.1"
            next_slave_id = next_slave_id or 1
            update_data["gateway_ip"] = next_gateway_ip
            update_data["slave_id"] = next_slave_id
        if not next_gateway_ip:
            raise HTTPException(status_code=400, detail="Modbus设备必须提供gateway_ip")
        if next_slave_id is None:
            raise HTTPException(status_code=400, detail="Modbus设备必须提供slave_id")
        update_data["device_secret"] = None
    else:
        raise HTTPException(status_code=400, detail="不支持的协议类型")

    for field, value in update_data.items():
        setattr(device, field, value)

    db.commit()
    db.refresh(device)

    device_secret_manager.update_device(
        device.device_id,
        device.device_secret,
        device.protocol_type
    )

    logger.info(f"更新设备成功: {device.device_id}")
    return device


@router.delete("/{device_id}")
async def delete_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除设备"""
    result = db.execute(select(Device).where(Device.device_id == device_id, Device.deleted_at.is_(None)))
    device = result.scalar_one_or_none()
    
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    
    device.is_active = False
    device.online_status = False
    device.deleted_at = datetime.now()
    db.commit()
    
    device_secret_manager.remove_device(device_id)
    
    logger.info(f"软删除设备成功: {device_id}")
    return {"message": "设备已归档，历史数据和告警记录已保留"}


@router.post("/{device_id}/sensors", response_model=SensorResponse)
async def create_sensor(
    device_id: str,
    sensor_data: SensorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """为设备创建传感器"""
    result = db.execute(select(Device).where(Device.device_id == device_id, Device.deleted_at.is_(None)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="设备不存在")
    
    validate_sensor_constraints(
        db=db,
        device_id=device_id,
        field_en=sensor_data.field_en,
        channel_no=sensor_data.channel_no,
        lower_threshold=sensor_data.lower_threshold,
        upper_threshold=sensor_data.upper_threshold,
    )
    
    sensor = Sensor(
        device_id=device_id,
        **sensor_data.dict()
    )
    db.add(sensor)
    db.commit()
    db.refresh(sensor)
    
    logger.info(f"创建设备传感器成功: {device_id}.{sensor.field_en}")
    return sensor


@router.get("/{device_id}/sensors", response_model=List[SensorResponse])
async def get_device_sensors(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取设备的所有传感器"""
    result = db.execute(
        select(Sensor)
        .where(Sensor.device_id == device_id)
        .order_by(Sensor.channel_no)
    )
    
    return result.scalars().all()


@router.put("/{device_id}/sensors/{sensor_id}", response_model=SensorResponse)
async def update_sensor(
    device_id: str,
    sensor_id: int,
    sensor_data: SensorUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新设备传感器"""
    result = db.execute(
        select(Sensor).where(
            Sensor.id == sensor_id,
            Sensor.device_id == device_id
        )
    )
    sensor = result.scalar_one_or_none()

    if not sensor:
        raise HTTPException(status_code=404, detail="传感器不存在")

    update_data = sensor_data.dict(exclude_unset=True)

    validate_sensor_constraints(
        db=db,
        device_id=device_id,
        field_en=update_data.get("field_en", sensor.field_en),
        channel_no=update_data.get("channel_no", sensor.channel_no),
        lower_threshold=update_data.get("lower_threshold", sensor.lower_threshold),
        upper_threshold=update_data.get("upper_threshold", sensor.upper_threshold),
        current_sensor_id=sensor_id,
    )

    for field, value in update_data.items():
        setattr(sensor, field, value)

    db.commit()
    db.refresh(sensor)

    logger.info(f"更新设备传感器成功: {device_id}.{sensor.field_en}")
    return sensor


@router.delete("/{device_id}/sensors/{sensor_id}")
async def delete_sensor(
    device_id: str,
    sensor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除设备传感器"""
    result = db.execute(
        select(Sensor).where(
            Sensor.id == sensor_id,
            Sensor.device_id == device_id
        )
    )
    sensor = result.scalar_one_or_none()

    if not sensor:
        raise HTTPException(status_code=404, detail="传感器不存在")

    db.execute(delete(Sensor).where(Sensor.id == sensor_id))
    db.commit()

    logger.info(f"删除设备传感器成功: {device_id}.{sensor.field_en}")
    return {"message": "传感器删除成功"}
