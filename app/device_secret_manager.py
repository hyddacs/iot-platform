"""
设备密钥管理器 - 根据协议类型变化调整
"""
import threading
import logging
from typing import Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import Device

logger = logging.getLogger(__name__)


class DeviceSecretManager:
    """设备密钥管理器"""
    
    def __init__(self):
        # 设备ID到密钥的映射
        self.device_secrets: Dict[str, str] = {}
        # 设备ID到协议类型的映射
        self.device_protocols: Dict[str, str] = {}
        # 读写锁
        self.lock = threading.RLock()
    
    def load_all_devices(self, db: Session) -> int:
        """从数据库加载所有设备到缓存"""
        with self.lock:
            try:
                # 查询所有设备
                stmt = select(Device.device_id, Device.device_secret, Device.protocol_type).where(
                    Device.deleted_at.is_(None),
                    Device.is_active == True
                )
                result = db.execute(stmt)
                devices = result.fetchall()
                
                # 清空缓存
                self.device_secrets.clear()
                self.device_protocols.clear()
                
                # 填充缓存
                for device_id, device_secret, protocol_type in devices:
                    if protocol_type == "http_active" and device_secret:  # 修改为http_active
                        self.device_secrets[device_id] = device_secret
                    self.device_protocols[device_id] = protocol_type
                
                count = len(devices)
                logger.info(f"📦 加载了 {count} 个设备到密钥缓存")
                return count
                
            except Exception as e:
                logger.error(f"加载设备密钥失败: {e}")
                return 0
    
    def verify_device(self, device_id: str, device_secret: str) -> bool:
        """验证设备ID和密钥"""
        with self.lock:
            # 检查设备是否存在
            if device_id not in self.device_protocols:
                logger.warning(f"设备 {device_id} 不存在")
                return False
            
            # 检查协议类型
            if self.device_protocols[device_id] != "http_active":  # 修改为http_active
                logger.warning(f"设备 {device_id} 不是HTTP主动上报设备")
                return False
            
            # 验证密钥
            stored_secret = self.device_secrets.get(device_id)
            if not stored_secret or stored_secret != device_secret:
                logger.warning(f"设备 {device_id} 密钥验证失败")
                return False
            
            logger.debug(f"✅ 设备 {device_id} 验证成功")
            return True
    
    def update_device(self, device_id: str, device_secret: Optional[str], protocol_type: str):
        """更新缓存中的设备"""
        with self.lock:
            if protocol_type == "http_active" and device_secret:  # 修改为http_active
                self.device_secrets[device_id] = device_secret
            elif device_id in self.device_secrets:
                del self.device_secrets[device_id]
            
            self.device_protocols[device_id] = protocol_type
            logger.info(f"🔄 更新设备缓存: {device_id}")
    
    def remove_device(self, device_id: str):
        """从缓存删除设备"""
        with self.lock:
            if device_id in self.device_secrets:
                del self.device_secrets[device_id]
            if device_id in self.device_protocols:
                del self.device_protocols[device_id]
            logger.info(f"🗑️ 从缓存删除设备: {device_id}")


# 全局实例
device_secret_manager = DeviceSecretManager()
