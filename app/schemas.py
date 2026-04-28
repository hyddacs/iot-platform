"""
Pydantic数据模型 - 根据最新表结构调整
"""
from pydantic import BaseModel, Field, validator, constr, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from typing import Literal


class Token(BaseModel):
    """令牌响应"""
    access_token: str
    token_type: str = "bearer"


class UserBase(BaseModel):
    """用户基础模型"""
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class UserCreate(UserBase):
    """用户创建模型"""
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    """用户登录模型"""
    username: str
    password: str


class UserResponse(UserBase):
    """用户响应模型"""
    id: int
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class DeviceBase(BaseModel):
    """设备基础模型"""
    device_id: constr(min_length=1, max_length=100)
    device_name: str = Field(..., min_length=1, max_length=100)
    protocol_type: Literal["modbus_gateway", "http_active"]  # 修改为http_active
    description: Optional[str] = None
    is_active: bool = True
    is_maintenance: bool = False
    is_test_device: bool = False
    offline_threshold: int = Field(900, ge=1, le=86400)
    factory: Optional[str] = Field(None, max_length=100)
    workshop: Optional[str] = Field(None, max_length=100)
    production_line: Optional[str] = Field(None, max_length=100)
    device_group: Optional[str] = Field(None, max_length=100)


class DeviceCreate(DeviceBase):
    """设备创建模型 - 根据表约束调整验证逻辑"""
    device_secret: Optional[str] = None
    gateway_ip: Optional[str] = None
    slave_id: Optional[int] = Field(None, ge=1, le=247)

    @model_validator(mode="after")
    def validate_protocol_fields(self):
        """验证协议专属字段，避免创建无法被轮询的 Modbus 设备。"""
        if self.protocol_type == "modbus_gateway" and not self.is_test_device:
            if not self.gateway_ip:
                raise ValueError("Modbus设备必须提供gateway_ip")
            if self.slave_id is None:
                raise ValueError("Modbus设备必须提供slave_id")
        return self
    
    @validator('device_secret')
    def validate_http_fields(cls, v, values):
        """验证HTTP设备字段 - 修改为http_active"""
        if values.get('protocol_type') == 'http_active':
            if v is None or v == "":
                # 自动生成密钥的逻辑在路由中处理
                pass
        elif v is not None:
            # Modbus设备不应有device_secret
            raise ValueError('Modbus设备不应提供device_secret')
        return v


class DeviceUpdate(BaseModel):
    """设备更新模型"""
    device_name: Optional[str] = Field(None, min_length=1, max_length=100)
    protocol_type: Optional[Literal["modbus_gateway", "http_active"]] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_maintenance: Optional[bool] = None
    is_test_device: Optional[bool] = None
    offline_threshold: Optional[int] = Field(None, ge=1, le=86400)
    factory: Optional[str] = Field(None, max_length=100)
    workshop: Optional[str] = Field(None, max_length=100)
    production_line: Optional[str] = Field(None, max_length=100)
    device_group: Optional[str] = Field(None, max_length=100)
    gateway_ip: Optional[str] = None
    slave_id: Optional[int] = Field(None, ge=1, le=247)
    device_secret: Optional[str] = None

    @validator('device_secret')
    def validate_device_secret_for_protocol(cls, v, values):
        if values.get('protocol_type') == 'modbus_gateway' and v is not None:
            raise ValueError('Modbus设备不应提供device_secret')
        return v


class DeviceResponse(DeviceBase):
    """设备响应模型"""
    id: int
    online_status: bool
    last_active_time: Optional[datetime]
    device_secret: Optional[str] = None
    gateway_ip: Optional[str] = None
    slave_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class DeviceListItem(DeviceResponse):
    """设备列表项，包含传感器摘要，避免前端逐台查询。"""
    sensor_count: int = 0
    sensor_preview: str = ""


class DeviceListResponse(BaseModel):
    """设备分页列表响应。"""
    items: List[DeviceListItem]
    total: int
    limit: int
    offset: int


class SensorBase(BaseModel):
    """传感器基础模型"""
    field_en: str = Field(..., min_length=1, max_length=50)
    field_cn: str = Field(..., min_length=1, max_length=50)
    unit: Optional[str] = None
    channel_no: Optional[int] = Field(None, ge=0, le=65535)
    coeff: float = 1.0
    lower_threshold: Optional[float] = None
    upper_threshold: Optional[float] = None
    is_monitored: bool = True


class SensorCreate(SensorBase):
    """传感器创建模型"""
    @validator('upper_threshold')
    def validate_threshold_range(cls, v, values):
        lower = values.get('lower_threshold')
        if lower is not None and v is not None and lower > v:
            raise ValueError('下限阈值不能大于上限阈值')
        return v


class DeviceCreateWithSensors(DeviceCreate):
    """带传感器列表的设备创建模型"""
    sensors: List[SensorCreate] = Field(default_factory=list)


class SensorUpdate(BaseModel):
    """传感器更新模型"""
    field_en: Optional[str] = Field(None, min_length=1, max_length=50)
    field_cn: Optional[str] = Field(None, min_length=1, max_length=50)
    unit: Optional[str] = None
    channel_no: Optional[int] = Field(None, ge=0, le=65535)
    coeff: Optional[float] = None
    lower_threshold: Optional[float] = None
    upper_threshold: Optional[float] = None
    is_monitored: Optional[bool] = None

    @validator('upper_threshold')
    def validate_threshold_range(cls, v, values):
        lower = values.get('lower_threshold')
        if lower is not None and v is not None and lower > v:
            raise ValueError('下限阈值不能大于上限阈值')
        return v


class SensorResponse(SensorBase):
    """传感器响应模型"""
    id: int
    device_id: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class HttpDataReport(BaseModel):  # 类名从G456DataReport改为HttpDataReport
    """HTTP主动上报设备数据上报模型"""
    device_id: str
    device_secret: str
    timestamp: Optional[str] = None
    data: Dict[str, Any]
    
    @validator('timestamp', pre=True, always=True)
    def set_timestamp(cls, v):
        """设置时间戳"""
        if v is None:
            return datetime.now().isoformat()
        return v


class HistoricalDataQuery(BaseModel):
    """历史数据查询模型"""
    device_id: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: int = Field(100, ge=1, le=1000)
    offset: int = Field(0, ge=0)
    include_total: bool = True


class HistoricalDataRecord(BaseModel):
    id: int
    device_id: str
    data: Dict[str, Any]
    reported_at: Optional[datetime] = None
    created_at: datetime


class HistoricalDataResponse(BaseModel):
    items: List[HistoricalDataRecord]
    total: int
    limit: int
    offset: int


class HistoricalAggregateQuery(BaseModel):
    """历史聚合查询模型。"""
    device_id: str
    field_en: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    bucket: Literal["auto", "minute", "hour"] = "auto"
    limit: int = Field(2000, ge=1, le=10000)
    include_total: bool = True


class HistoricalAggregateRecord(BaseModel):
    id: str
    device_id: str
    bucket_time: datetime
    data: Dict[str, float]
    min_data: Dict[str, float]
    max_data: Dict[str, float]
    count_data: Dict[str, int]
    reported_at: datetime
    created_at: datetime
    source: str


class HistoricalAggregateResponse(BaseModel):
    items: List[HistoricalAggregateRecord]
    total: int
    limit: int
    bucket: str


class StatsResponse(BaseModel):
    """统计响应模型 - 修改字段名为http_devices"""
    total_devices: int
    online_devices: int
    offline_devices: int
    modbus_devices: int
    http_devices: int  # 从g456_devices改为http_devices
    total_alerts_unresolved: int
    upper_alerts_unresolved: int = 0
    lower_alerts_unresolved: int = 0
    alert_devices_unresolved: int = 0
    alert_fields_unresolved: int = 0


class AlertResponse(BaseModel):
    """告警响应模型 - 新增字段"""
    id: int
    device_id: str
    device_name: Optional[str] = None
    sensor_field_en: str
    sensor_field_cn: str
    current_value: float
    threshold_value: float
    alert_type: str
    alert_level: str
    status: str = "active"
    first_triggered_at: Optional[datetime] = None
    last_triggered_at: Optional[datetime] = None
    occurrence_count: int = 1
    recovered_at: Optional[datetime] = None
    is_resolved: bool
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[int] = None
    resolved_by_name: Optional[str] = None
    resolve_note: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class AlertResolveRequest(BaseModel):
    """告警处理请求"""
    resolve_note: Optional[str] = Field(None, max_length=255)


class AlertListResponse(BaseModel):
    """告警分页列表响应。"""
    items: List[AlertResponse]
    total: int
    limit: int
    offset: int


class AlertSummaryResponse(BaseModel):
    """告警聚合统计响应。"""
    total: int
    unresolved: int
    resolved: int
    upper: int
    lower: int
    recovered: int
    affected_devices: int
