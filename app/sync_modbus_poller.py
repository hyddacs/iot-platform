"""
同步Modbus轮询器 - 修改设备类型判断
"""
import time
import threading
import logging
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Set
import random
from pymodbus.client import ModbusTcpClient
from sqlalchemy.orm import Session
from sqlalchemy import update, select
from app.models import Device, Sensor, DeviceData, Alert
from app.services.data_ingest import persist_sensor_points_and_aggregates
from app.services.alerts import resolve_recovered_alerts, upsert_active_alerts
from app.websocket_manager import websocket_manager
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class SyncModbusPoller:
    """同步Modbus轮询器"""
    
    def __init__(self, poll_interval: int = 3):
        self.poll_interval = poll_interval
        self.is_running = False
        self.poll_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        
        # 网关客户端缓存
        self.gateway_clients: Dict[str, ModbusTcpClient] = {}
        self.gateway_status: Dict[str, dict] = {}
        self.last_cycle_at: Optional[datetime] = None
        self.last_cycle_error: Optional[str] = None
    
    def start(self, get_db_session):
        """启动轮询器"""
        if self.is_running:
            return
        
        self.is_running = True
        self.stop_event.clear()
        self.get_db_session = get_db_session
        
        # 启动轮询线程
        self.poll_thread = threading.Thread(
            target=self._poll_worker,
            name="ModbusPoller",
            daemon=True
        )
        self.poll_thread.start()
        
        logger.info(f"🚀 Modbus轮询器已启动，轮询间隔: {self.poll_interval}秒")
    
    def stop(self):
        """停止轮询器"""
        self.is_running = False
        self.stop_event.set()
        
        if self.poll_thread:
            self.poll_thread.join(timeout=5)
        
        # 关闭所有Modbus连接
        for client in self.gateway_clients.values():
            if client.connected:
                client.close()
        
        logger.info("🛑 Modbus轮询器已停止")
    
    def _poll_worker(self):
        """轮询工作线程"""
        logger.info("🔄 Modbus轮询线程开始运行")
        
        while self.is_running and not self.stop_event.is_set():
            cycle_start = time.time()
            
            try:
                # 执行一个完整的轮询周期
                self._single_poll_cycle()
                self.last_cycle_at = datetime.now()
                self.last_cycle_error = None
                
            except Exception as e:
                self.last_cycle_error = str(e)
                logger.error(f"轮询周期异常: {e}")
            
            elapsed = time.time() - cycle_start
            sleep_time = self.poll_interval - elapsed
            
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def _single_poll_cycle(self):
        """执行单个轮询周期"""
        with self.get_db_session() as db:
            # 1. 加载设备配置
            device_configs, sensor_maps = self._load_device_config(db)
            
            if not device_configs:
                return
            
            # 2. 收集本轮所有数据
            all_device_data = []  # 设备数据列表
            alerts_to_insert = []  # 告警列表
            device_ids_processed = set()  # 处理过的设备ID

            # 3. 按网关分组设备，测试设备直接模拟
            devices_by_gateway = defaultdict(list)
            for device_id, device_config in device_configs.items():
                if device_config.get("is_test_device"):
                    simulated_result = self._simulate_single_device(
                        device_id,
                        sensor_maps.get(device_id, {}),
                        device_config.get("is_maintenance", False)
                    )
                    self._collect_device_result(
                        simulated_result,
                        device_id,
                        all_device_data,
                        alerts_to_insert,
                        device_ids_processed
                    )
                    continue

                gateway_ip = device_config["gateway_ip"]
                if gateway_ip:
                    devices_by_gateway[gateway_ip].append({
                        "device_id": device_id,
                        "slave_id": device_config["slave_id"],
                        "is_maintenance": device_config.get("is_maintenance", False),
                        "sensor_map": sensor_maps.get(device_id, {})
                    })
            
            # 4. 处理真实网关设备
            for gateway_ip, devices in devices_by_gateway.items():
                gateway_data = self._poll_gateway(gateway_ip, devices)
                
                if gateway_data:
                    all_device_data.extend(gateway_data["device_data"])
                    alerts_to_insert.extend(gateway_data["alerts"])
                    device_ids_processed.update(gateway_data["device_ids"])
            
            self._persist_and_broadcast_results(
                db=db,
                device_data_list=all_device_data,
                alerts=alerts_to_insert,
                device_ids=device_ids_processed
            )

    def _collect_device_result(
        self,
        device_result: Optional[dict],
        device_id: str,
        all_device_data: List[dict],
        alerts_to_insert: List[dict],
        device_ids_processed: Set[str]
    ):
        """把单设备轮询或模拟结果并入当前周期结果集。"""
        if not device_result:
            return

        all_device_data.append(device_result["device_data"])
        alerts_to_insert.extend(device_result["alerts"])
        device_ids_processed.add(device_id)
    
    def _load_device_config(self, db: Session) -> Tuple[Dict, Dict]:
        """加载设备配置"""
        # 查询所有在线的Modbus设备
        stmt = select(Device).where(
            Device.protocol_type == "modbus_gateway",  # 注意：协议类型不变
            Device.is_active == True,
            Device.deleted_at.is_(None)
        )
        devices = db.execute(stmt).scalars().all()
        
        device_configs = {}
        sensor_maps = {}
        
        device_ids = [device.device_id for device in devices]
        sensors_by_device = defaultdict(list)
        if device_ids:
            sensors = db.execute(
                select(Sensor).where(
                    Sensor.device_id.in_(device_ids),
                    Sensor.is_monitored == True,
                    Sensor.channel_no.isnot(None)
                )
            ).scalars().all()
            for sensor in sensors:
                sensors_by_device[sensor.device_id].append(sensor)

        for device in devices:
            device_sensors = sensors_by_device.get(device.device_id, [])
            if not device_sensors:
                continue
            
            # 构建传感器映射
            sensor_map = {}
            for sensor in device_sensors:
                sensor_map[sensor.channel_no] = {
                    "sensor_id": sensor.id,
                    "field_en": sensor.field_en,
                    "field_cn": sensor.field_cn,
                    "coeff": sensor.coeff,
                    "unit": sensor.unit,
                    "lower_threshold": sensor.lower_threshold,
                    "upper_threshold": sensor.upper_threshold
                }
            
            # 设备配置
            device_configs[device.device_id] = {
                "gateway_ip": device.gateway_ip,
                "slave_id": device.slave_id,
                "device_name": device.device_name,
                "is_test_device": device.is_test_device,
                "is_maintenance": device.is_maintenance
            }
            
            # 传感器映射
            sensor_maps[device.device_id] = sensor_map
        
        return device_configs, sensor_maps
    
    def _poll_gateway(self, gateway_ip: str, devices: List[dict]) -> Optional[dict]:
        """轮询一个网关下的所有设备"""
        result = {
            "device_data": [],
            "alerts": [],
            "device_ids": set()
        }
        
        # 获取或创建网关客户端
        client = self._get_gateway_client(gateway_ip)
        if not client or not client.connected:
            logger.error(f"网关 {gateway_ip} 连接失败")
            self._mark_gateway_status(gateway_ip, False, "连接失败")
            return None
        
        try:
            # 处理每个设备
            for device_info in devices:
                device_result = self._poll_single_device(client, device_info)
                if device_result:
                    result["device_data"].append(device_result["device_data"])
                    result["alerts"].extend(device_result["alerts"])
                    result["device_ids"].add(device_info["device_id"])
            
            self._mark_gateway_status(gateway_ip, True, "")
            return result
            
        except Exception as e:
            logger.error(f"轮询网关 {gateway_ip} 异常: {e}")
            # 网关连接可能已失效，移除客户端
            if gateway_ip in self.gateway_clients:
                self.gateway_clients[gateway_ip].close()
                del self.gateway_clients[gateway_ip]
            self._mark_gateway_status(gateway_ip, False, str(e))
            return None
    
    def _get_gateway_client(self, gateway_ip: str) -> Optional[ModbusTcpClient]:
        """获取或创建网关客户端"""
        if gateway_ip not in self.gateway_clients:
            try:
                client = ModbusTcpClient(
                    host=gateway_ip,
                    port=settings.MODBUS_PORT,
                    timeout=settings.MODBUS_TIMEOUT,
                    retries=1
                )
                
                if client.connect():
                    self.gateway_clients[gateway_ip] = client
                    logger.debug(f"🔗 连接到Modbus网关: {gateway_ip}")
                else:
                    logger.error(f"无法连接到Modbus网关: {gateway_ip}:{settings.MODBUS_PORT}")
                    self._mark_gateway_status(gateway_ip, False, "无法连接")
                    return None
                    
            except Exception as e:
                logger.error(f"创建Modbus客户端失败: {e}")
                self._mark_gateway_status(gateway_ip, False, str(e))
                return None
        
        return self.gateway_clients[gateway_ip]
    
    def _mark_gateway_status(self, gateway_ip: str, connected: bool, error: str):
        self.gateway_status[gateway_ip] = {
            "connected": connected,
            "last_checked_at": datetime.now().isoformat(),
            "last_error": error
        }

    def _poll_single_device(self, client: ModbusTcpClient, device_info: dict) -> Optional[dict]:
        """轮询单个设备"""
        device_id = device_info["device_id"]
        slave_id = device_info["slave_id"]
        sensor_map = device_info["sensor_map"]
        is_maintenance = device_info.get("is_maintenance", False)
        
        if not sensor_map:
            return None
        
        try:
            # 计算最大通道号
            max_channel = max(sensor_map.keys())
            
            # 批量读取寄存器
            response = client.read_holding_registers(
                0,  # 起始地址
                max_channel + 1,  # 寄存器数量
                slave=slave_id
            )
            
            if response.isError():
                logger.error(f"读取设备 {device_id} 寄存器失败")
                return None
            
            registers = response.registers
            
            # 构建数据
            json_data = {"device_id": device_id}
            raw_map = {}
            alerts = []
            
            for channel_no, sensor_config in sensor_map.items():
                if channel_no < len(registers):
                    # 原始值
                    raw_value = registers[channel_no]
                    raw_map[channel_no] = raw_value
                    
                    # 应用系数转换
                    actual_value = raw_value * sensor_config["coeff"]
                    formatted_value = round(actual_value, 3)
                    
                    # 添加到JSON数据
                    json_data[sensor_config["field_en"]] = formatted_value
                    
                    # 检查阈值告警
                    if not is_maintenance:
                        alert = self._check_sensor_threshold(
                            device_id, sensor_config, actual_value
                        )
                        if alert:
                            alerts.append(alert)
            
            return {
                "device_data": {
                    "device_id": device_id,
                    "json_data": json_data,
                    "raw_map": raw_map,
                    "timestamp": datetime.now()
                },
                "alerts": alerts
            }
            
        except Exception as e:
            logger.error(f"处理设备 {device_id} 数据失败: {e}")
            return None

    def _simulate_single_device(self, device_id: str, sensor_map: dict, is_maintenance: bool = False) -> Optional[dict]:
        """为测试设备模拟一次与真实轮询结构一致的结果。"""
        if not sensor_map:
            return None

        try:
            json_data = {"device_id": device_id}
            raw_map = {}
            alerts = []

            for channel_no, sensor_config in sensor_map.items():
                actual_value = self._build_threshold_safe_value_from_config(sensor_config)
                coeff = sensor_config.get("coeff") or 1.0
                raw_map[channel_no] = round(actual_value / coeff, 3)
                json_data[sensor_config["field_en"]] = round(actual_value, 3)

                if not is_maintenance:
                    alert = self._check_sensor_threshold(device_id, sensor_config, actual_value)
                    if alert:
                        alerts.append(alert)

            return {
                "device_data": {
                    "device_id": device_id,
                    "json_data": json_data,
                    "raw_map": raw_map,
                    "timestamp": datetime.now()
                },
                "alerts": alerts
            }
        except Exception as e:
            logger.error(f"模拟测试设备 {device_id} 数据失败: {e}")
            return None
    
    def _check_sensor_threshold(self, device_id: str, sensor_config: dict, value: float) -> Optional[dict]:
        """检查传感器阈值"""
        lower_threshold = sensor_config.get("lower_threshold")
        upper_threshold = sensor_config.get("upper_threshold")
        
        # 检查下限阈值
        if lower_threshold is not None and value < lower_threshold:
            return {
                "device_id": device_id,
                "sensor_field_en": sensor_config["field_en"],
                "sensor_field_cn": sensor_config["field_cn"],
                "current_value": value,
                "threshold_value": lower_threshold,
                "alert_type": "LOWER_LIMIT",
                "alert_level": "WARNING"
            }
        
        # 检查上限阈值
        elif upper_threshold is not None and value > upper_threshold:
            return {
                "device_id": device_id,
                "sensor_field_en": sensor_config["field_en"],
                "sensor_field_cn": sensor_config["field_cn"],
                "current_value": value,
                "threshold_value": upper_threshold,
                "alert_type": "UPPER_LIMIT",
                "alert_level": "WARNING"
            }
        
        return None
    
    def _batch_insert_device_data(self, db: Session, device_data_list: List[dict]):
        """批量插入设备数据 - 修改为包含reported_at"""
        if not device_data_list:
            return
        
        try:
            # 准备批量插入数据
            data_records = []
            for data in device_data_list:
                data_records.append(
                    DeviceData(
                        device_id=data["device_id"],
                        reported_at=data.get("timestamp", datetime.now()),
                        data_json=data["json_data"]
                    )
                )
            
            # 批量添加
            db.add_all(data_records)
            db.flush()

            sensors_by_device = self._load_sensors_for_payloads(db, device_data_list)
            for record, data in zip(data_records, device_data_list):
                payload = {
                    field: value
                    for field, value in data["json_data"].items()
                    if field != "device_id"
                }
                persist_sensor_points_and_aggregates(
                    db,
                    device_id=data["device_id"],
                    device_data_id=record.id,
                    data=payload,
                    sensors=sensors_by_device.get(data["device_id"], []),
                    reported_at=data.get("timestamp", datetime.now()),
                )
            
        except Exception as e:
            logger.error(f"批量插入设备数据失败: {e}")
            raise

    def _load_sensors_for_payloads(self, db: Session, device_data_list: List[dict]) -> Dict[str, List[Sensor]]:
        device_ids = {data["device_id"] for data in device_data_list}
        if not device_ids:
            return {}

        sensors = db.execute(
            select(Sensor).where(
                Sensor.device_id.in_(device_ids),
                Sensor.is_monitored == True
            )
        ).scalars().all()

        sensors_by_device: Dict[str, List[Sensor]] = defaultdict(list)
        for sensor in sensors:
            sensors_by_device[sensor.device_id].append(sensor)
        return sensors_by_device
    
    def _batch_update_device_status(self, db: Session, device_ids: set):
        """批量更新设备状态"""
        if not device_ids:
            return
        
        try:
            # 批量更新
            db.execute(
                update(Device)
                .where(Device.device_id.in_(list(device_ids)))
                .values(
                    online_status=True,
                    last_active_time=datetime.now()
                )
            )
            
        except Exception as e:
            logger.error(f"批量更新设备状态失败: {e}")
            raise
    
    def _batch_insert_alerts(self, db: Session, alerts: List[dict]):
        """批量插入告警 - 包含新增字段"""
        if not alerts:
            return
        
        try:
            # 准备告警记录
            alert_records = []
            for alert in alerts:
                alert_records.append(
                    Alert(
                        device_id=alert["device_id"],
                        sensor_field_en=alert["sensor_field_en"],
                        sensor_field_cn=alert["sensor_field_cn"],
                        current_value=alert["current_value"],
                        threshold_value=alert["threshold_value"],
                        alert_type=alert["alert_type"],
                        alert_level=alert["alert_level"]
                    )
                )
            
            # 批量添加
            db.add_all(alert_records)
            
        except Exception as e:
            logger.error(f"批量插入告警失败: {e}")
            raise

    def _persist_and_broadcast_results(
        self,
        db: Session,
        device_data_list: List[dict],
        alerts: List[dict],
        device_ids: Set[str]
    ):
        """统一处理轮询结果的批量入库与 WebSocket 推送。"""
        if not device_data_list:
            return

        self._batch_insert_device_data(db, device_data_list)

        if device_ids:
            self._batch_update_device_status(db, device_ids)

        changed_alerts = upsert_active_alerts(db, alerts)
        recovered_alerts = self._resolve_recovered_alerts(db, device_data_list, alerts)

        db.commit()

        try:
            self._broadcast_poll_results(device_data_list, changed_alerts + recovered_alerts)
        except Exception as e:
            logger.error(f"轮询结果WebSocket推送失败: {e}")

        logger.info(f"✅ 周期完成: 处理 {len(device_ids)} 个设备，{len(device_data_list)} 条数据")

    def _resolve_recovered_alerts(self, db: Session, device_data_list: List[dict], alerts: List[dict]) -> List[dict]:
        alerted_fields = defaultdict(set)
        for alert in alerts:
            alerted_fields[alert["device_id"]].add(alert["sensor_field_en"])

        recovered = []
        for data in device_data_list:
            device_id = data["device_id"]
            fields = {
                field
                for field in data["json_data"].keys()
                if field != "device_id" and field not in alerted_fields[device_id]
            }
            recovered.extend(resolve_recovered_alerts(db, device_id, fields))

        return recovered

    def _broadcast_poll_results(self, device_data_list: List[dict], alerts: List[dict]):
        """按原有实时推送方式广播轮询结果。"""
        for data in device_data_list:
            websocket_manager.dispatch_device_data(
                data["device_id"],
                data["json_data"]
            )

        for alert in alerts:
            websocket_manager.dispatch_alert(alert)

    def get_status(self) -> dict:
        """返回轮询器运行和网关健康信息，供前端展示故障原因。"""
        return {
            "status": "running" if self.is_running else "stopped",
            "interval": self.poll_interval,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_cycle_error": self.last_cycle_error,
            "gateways": self.gateway_status
        }

    def insert_threshold_safe_test_data(
        self,
        db: Session,
        device_id: str,
        reported_at: Optional[datetime] = None
    ) -> dict:
        """模拟一次指定 Modbus 设备的轮询，并按正常链路批量入库与广播。"""
        device = db.execute(
            select(Device).where(
                Device.device_id == device_id,
                Device.protocol_type == "modbus_gateway",
                Device.deleted_at.is_(None)
            )
        ).scalar_one_or_none()

        if not device:
            raise ValueError("设备不存在或不是Modbus设备")

        sensors = db.execute(
            select(Sensor).where(
                Sensor.device_id == device_id,
                Sensor.is_monitored == True,
                Sensor.channel_no.isnot(None)
            ).order_by(Sensor.channel_no)
        ).scalars().all()

        if not sensors:
            raise ValueError("设备没有可用于模拟的传感器")

        sensor_map = {}
        payload = {"device_id": device_id}
        raw_map = {}
        alerts = []

        for sensor in sensors:
            sensor_config = {
                "sensor_id": sensor.id,
                "field_en": sensor.field_en,
                "field_cn": sensor.field_cn,
                "coeff": sensor.coeff,
                "unit": sensor.unit,
                "lower_threshold": sensor.lower_threshold,
                "upper_threshold": sensor.upper_threshold
            }
            sensor_map[sensor.channel_no] = sensor_config

            actual_value = self._build_threshold_safe_value(sensor)
            coeff = sensor.coeff if sensor.coeff not in (None, 0) else 1.0
            raw_value = round(actual_value / coeff, 3)
            raw_map[sensor.channel_no] = raw_value
            payload[sensor.field_en] = round(actual_value, 3)

            if not device.is_maintenance:
                alert = self._check_sensor_threshold(device_id, sensor_config, actual_value)
                if alert:
                    alerts.append(alert)

        timestamp = reported_at or datetime.now()
        poll_result = {
            "device_data": [
                {
                    "device_id": device_id,
                    "json_data": payload,
                    "raw_map": raw_map,
                    "timestamp": timestamp
                }
            ],
            "alerts": alerts,
            "device_ids": {device_id}
        }

        self._persist_and_broadcast_results(
            db=db,
            device_data_list=poll_result["device_data"],
            alerts=poll_result["alerts"],
            device_ids=poll_result["device_ids"]
        )
        logger.info(f"🧪 已按轮询链路为设备 {device_id} 生成并推送测试数据")

        return {
            "device_id": device_id,
            "reported_at": timestamp.isoformat(),
            "data": payload,
            "alerts": alerts,
            "raw_map": raw_map
        }

    def _build_threshold_safe_value(self, sensor: Sensor) -> float:
        """生成落在阈值范围内的测试值。"""
        lower = sensor.lower_threshold
        upper = sensor.upper_threshold

        if lower is not None and upper is not None:
            return (lower + upper) / 2
        if lower is not None:
            return lower + max(abs(lower) * 0.1, 1.0)
        if upper is not None:
            return upper - max(abs(upper) * 0.1, 1.0)

        baseline = sensor.coeff if sensor.coeff not in (None, 0) else 1.0
        return random.uniform(1.0, 10.0) * abs(baseline)

    def _build_threshold_safe_value_from_config(self, sensor_config: dict) -> float:
        """根据传感器配置生成落在阈值范围内的测试值。"""
        lower = sensor_config.get("lower_threshold")
        upper = sensor_config.get("upper_threshold")

        if lower is not None and upper is not None:
            return (lower + upper) / 2
        if lower is not None:
            return lower + max(abs(lower) * 0.1, 1.0)
        if upper is not None:
            return upper - max(abs(upper) * 0.1, 1.0)

        baseline = sensor_config.get("coeff")
        baseline = baseline if baseline not in (None, 0) else 1.0
        return random.uniform(1.0, 10.0) * abs(baseline)


# 全局实例
modbus_poller = SyncModbusPoller(poll_interval=settings.MODBUS_POLL_INTERVAL)
