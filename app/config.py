"""
应用配置管理
"""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 应用配置
    APP_NAME: str = "工业物联网设备监控平台"
    DEBUG: bool = True
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # 数据库配置
    DATABASE_URL: str = "mysql+pymysql://root:password@localhost:3306/iot_platform"
    SQL_ECHO: bool = False
    
    # JWT配置
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Modbus轮询配置
    MODBUS_POLL_INTERVAL: int = 3
    MODBUS_TIMEOUT: int = 2
    MODBUS_PORT: int = 502
    
    # 设备配置
    DEVICE_TIMEOUT: int = 900

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug_value(cls, value):
        """兼容常见部署环境中的字符串布尔值。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return value
    
    class Config:
        env_file = ".env"
        extra = "ignore"  # 忽略额外字段，不报错


@lru_cache()
def get_settings() -> Settings:
    return Settings()
