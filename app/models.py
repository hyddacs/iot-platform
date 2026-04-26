"""
SQLAlchemy数据模型 - 根据最新表结构调整
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, Text, DateTime, JSON, Enum, BigInteger, ForeignKey, SmallInteger, CheckConstraint, Index
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base
import enum

Base = declarative_base()


def enum_values(enum_cls):
    """让 SQLAlchemy 使用枚举 value，而不是成员名，避免与数据库 ENUM 值不一致。"""
    return [member.value for member in enum_cls]


class ProtocolType(str, enum.Enum):
    """协议类型枚举 - 已修改为http_active"""
    MODBUS_GATEWAY = "modbus_gateway"
    HTTP_ACTIVE = "http_active"  # 从g456_active改为http_active


class AlertLevel(str, enum.Enum):
    """告警级别枚举"""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertType(str, enum.Enum):
    """告警类型枚举"""
    LOWER_LIMIT = "LOWER_LIMIT"
    UPPER_LIMIT = "UPPER_LIMIT"


class AlertStatus(str, enum.Enum):
    """告警生命周期状态"""
    ACTIVE = "active"
    RECOVERED = "recovered"
    RESOLVED = "resolved"


class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now(3))
    updated_at = Column(DateTime, server_default=func.now(3), onupdate=func.now(3))
    
    # 索引
    __table_args__ = (
        Index('idx_users_username', 'username'),
        Index('idx_users_email', 'email'),
    )


class Device(Base):
    """设备表 - 根据最新结构调整"""
    __tablename__ = "devices"
    
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), unique=True, nullable=False)
    device_name = Column(String(100), nullable=False)
    protocol_type = Column(
        Enum(
            ProtocolType,
            values_callable=enum_values,
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False
    )
    
    # HTTP主动上报设备字段
    device_secret = Column(String(255), nullable=True)
    
    # Modbus设备字段
    gateway_ip = Column(String(64), nullable=True)
    slave_id = Column(SmallInteger, nullable=True)
    
    # 通用字段
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    is_maintenance = Column(Boolean, default=False)
    is_test_device = Column(Boolean, default=False)
    online_status = Column(Boolean, default=False)
    last_active_time = Column(DateTime, nullable=True)
    offline_threshold = Column(Integer, default=900)
    factory = Column(String(100), nullable=True)
    workshop = Column(String(100), nullable=True)
    production_line = Column(String(100), nullable=True)
    device_group = Column(String(100), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, server_default=func.now(3))
    updated_at = Column(DateTime, server_default=func.now(3), onupdate=func.now(3))
    
    # 索引和约束
    __table_args__ = (
        Index('idx_devices_device_id', 'device_id'),
        Index('idx_devices_protocol_type', 'protocol_type'),
        Index('idx_devices_online_status', 'online_status'),
        Index('idx_devices_last_active_time', 'last_active_time'),
        Index('idx_devices_protocol_active', 'protocol_type', 'is_active'),
        Index('idx_devices_grouping', 'factory', 'workshop', 'production_line', 'device_group'),
        Index('idx_devices_deleted_at', 'deleted_at'),
    )


class Sensor(Base):
    """传感器配置表 - 根据最新结构调整"""
    __tablename__ = "sensors"
    
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(
        String(100),
        ForeignKey("devices.device_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )
    field_en = Column(String(50), nullable=False)
    field_cn = Column(String(50), nullable=False)
    unit = Column(String(20), nullable=True)
    
    # Modbus专用字段
    channel_no = Column(Integer, nullable=True)
    coeff = Column(Float, default=1.0)
    
    # 告警相关
    lower_threshold = Column(Float, nullable=True)
    upper_threshold = Column(Float, nullable=True)
    
    is_monitored = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now(3))
    updated_at = Column(DateTime, server_default=func.now(3), onupdate=func.now(3))
    
    # 索引和约束
    __table_args__ = (
        Index('idx_sensors_device_id', 'device_id'),
        Index('idx_sensors_device_monitored', 'device_id', 'is_monitored'),
        Index('uk_sensors_device_field_en', 'device_id', 'field_en', unique=True),
        Index('uk_sensors_device_channel_no', 'device_id', 'channel_no', unique=True),
        CheckConstraint(
            'lower_threshold IS NULL OR upper_threshold IS NULL OR lower_threshold <= upper_threshold',
            name='chk_sensors_threshold_range'
        ),
    )


class DeviceData(Base):
    """设备数据表 - 根据最新结构调整，新增reported_at字段"""
    __tablename__ = "device_data"
    
    id = Column(BigInteger, primary_key=True, index=True)
    device_id = Column(
        String(100),
        ForeignKey("devices.device_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )
    reported_at = Column(DateTime, nullable=True)  # 新增字段：设备上报时间
    data_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now(3))  # 平台入库时间
    
    # 索引
    __table_args__ = (
        Index('idx_device_data_device_time', 'device_id', 'created_at'),
        Index('idx_device_data_device_reported_time', 'device_id', 'reported_at'),
    )


class SensorDataPoint(Base):
    """传感器点位明细数据表。"""
    __tablename__ = "sensor_data_points"

    id = Column(BigInteger, primary_key=True, index=True)
    device_data_id = Column(BigInteger, nullable=True)
    device_id = Column(String(100), nullable=False)
    sensor_id = Column(BigInteger, nullable=False)
    field_en = Column(String(100), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(50), nullable=True)
    reported_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_device_time", "device_id", "reported_at"),
        Index("idx_sensor_time", "sensor_id", "reported_at"),
        Index("idx_field_time", "device_id", "field_en", "reported_at"),
        Index("idx_created_at", "created_at"),
        Index("idx_device_data_id", "device_data_id"),
    )


class SensorDataAggMin(Base):
    """传感器分钟聚合数据表。"""
    __tablename__ = "sensor_data_agg_min"

    id = Column(BigInteger, primary_key=True, index=True)
    device_id = Column(String(100), nullable=False)
    sensor_id = Column(BigInteger, nullable=False)
    field_en = Column(String(100), nullable=False)
    bucket_time = Column(DateTime, nullable=False)
    avg_value = Column(Float, nullable=True)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)
    first_val = Column(Float, nullable=True)
    last_val = Column(Float, nullable=True)
    count_value = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uk_sensor_minute", "sensor_id", "bucket_time", unique=True),
        Index("idx_device_minute", "device_id", "bucket_time"),
        Index("idx_field_minute", "device_id", "field_en", "bucket_time"),
        Index("idx_bucket_time", "bucket_time"),
    )


class SensorDataAggHour(Base):
    """传感器小时聚合数据表。"""
    __tablename__ = "sensor_data_agg_hour"

    id = Column(BigInteger, primary_key=True, index=True)
    device_id = Column(String(100), nullable=False)
    sensor_id = Column(BigInteger, nullable=False)
    field_en = Column(String(100), nullable=False)
    bucket_time = Column(DateTime, nullable=False)
    avg_value = Column(Float, nullable=True)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)
    first_val = Column(Float, nullable=True)
    last_val = Column(Float, nullable=True)
    count_value = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uk_sensor_hour", "sensor_id", "bucket_time", unique=True),
        Index("idx_device_hour", "device_id", "bucket_time"),
        Index("idx_field_hour", "device_id", "field_en", "bucket_time"),
        Index("idx_bucket_time", "bucket_time"),
    )


class Alert(Base):
    """告警记录表 - 根据最新结构调整"""
    __tablename__ = "alerts"
    
    id = Column(BigInteger, primary_key=True, index=True)
    device_id = Column(
        String(100),
        ForeignKey("devices.device_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )
    sensor_field_en = Column(String(50), nullable=False)
    sensor_field_cn = Column(String(50), nullable=False)
    current_value = Column(Float, nullable=False)
    threshold_value = Column(Float, nullable=False)
    alert_type = Column(
        Enum(
            AlertType,
            values_callable=enum_values,
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False
    )
    alert_level = Column(
        Enum(
            AlertLevel,
            values_callable=enum_values,
            native_enum=False,
            validate_strings=True,
        ),
        default=AlertLevel.WARNING
    )
    
    is_resolved = Column(Boolean, default=False)
    status = Column(String(20), default=AlertStatus.ACTIVE.value)
    first_triggered_at = Column(DateTime, nullable=True)
    last_triggered_at = Column(DateTime, nullable=True)
    occurrence_count = Column(Integer, default=1)
    recovered_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True
    )
    resolve_note = Column(String(255), nullable=True)  # 新增字段：解决备注
    
    created_at = Column(DateTime, server_default=func.now(3))
    updated_at = Column(DateTime, server_default=func.now(3), onupdate=func.now(3))
    
    # 索引
    __table_args__ = (
        Index('idx_alerts_device_time', 'device_id', 'created_at'),
        Index('idx_alerts_unresolved_time', 'is_resolved', 'created_at'),
        Index('idx_alerts_device_field_resolved', 'device_id', 'sensor_field_en', 'is_resolved'),
        Index('idx_alerts_active_lookup', 'device_id', 'sensor_field_en', 'alert_type', 'status', 'is_resolved'),
        Index('idx_alerts_type_resolved_time', 'alert_type', 'is_resolved', 'last_triggered_at'),
        Index('idx_alerts_status_time', 'status', 'last_triggered_at'),
        CheckConstraint(
            '(is_resolved = FALSE AND resolved_at IS NULL) OR (is_resolved = TRUE)',
            name='chk_alerts_resolved_time'
        ),
    )
