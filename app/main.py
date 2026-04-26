"""
工业物联网设备监控平台 - 主应用
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging
import threading
from datetime import datetime

from app.config import get_settings
from app.database import get_db_context, init_db
from app.device_secret_manager import device_secret_manager
from app.websocket_manager import websocket_manager
from app.sync_modbus_poller import modbus_poller
from app.routes import auth, devices, data, ws, monitor

settings = get_settings()

# 配置日志
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("sqlalchemy.engine").setLevel(
    logging.INFO if settings.SQL_ECHO else logging.WARNING
)

logger = logging.getLogger(__name__)


def check_device_status_periodically(stop_event: threading.Event):
    """每分钟检查一次设备在线状态"""
    from sqlalchemy import select
    from app.models import Device

    while not stop_event.is_set():
        try:
            with get_db_context() as db:
                # 查询所有设备
                devices = db.execute(select(Device).where(Device.deleted_at.is_(None))).scalars().all()
                now = datetime.now()
                
                for device in devices:
                    if device.last_active_time:
                        time_diff = (now - device.last_active_time).total_seconds()
                        should_be_online = time_diff < device.offline_threshold
                        
                        if device.online_status != should_be_online:
                            # 更新设备状态
                            device.online_status = should_be_online
                            websocket_manager.dispatch_device_status(
                                device.device_id,
                                should_be_online
                            )
                
                db.commit()

            stop_event.wait(60)  # 每分钟检查一次
            
        except Exception as e:
            logger.error(f"检查设备状态失败: {e}")
            stop_event.wait(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    status_thread_stop = threading.Event()
    status_thread = None

    # 启动时
    logger.info("🚀 正在启动工业物联网设备监控平台...")
    websocket_manager.set_event_loop(asyncio.get_running_loop())
    
    # 初始化数据库
    init_db()
    logger.info("✅ 数据库初始化完成")
    
    # 加载设备密钥到内存
    with get_db_context() as db:
        loaded = device_secret_manager.load_all_devices(db)
        logger.info(f"✅ 加载了 {loaded} 个设备密钥到缓存")
    
    # 启动Modbus轮询器
    modbus_poller.start(get_db_session=get_db_context)
    
    # 启动设备状态检查任务
    status_thread = threading.Thread(
        target=check_device_status_periodically,
        args=(status_thread_stop,),
        name="DeviceStatusChecker",
        daemon=True
    )
    status_thread.start()
    
    yield
    
    # 关闭时
    logger.info("🛑 正在关闭应用...")
    status_thread_stop.set()
    if status_thread is not None:
        status_thread.join(timeout=5)
    modbus_poller.stop()
    websocket_manager.set_event_loop(None)
    logger.info("✅ 应用关闭完成")


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    description="工业物联网设备监控平台",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# 添加中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["用户认证"])
app.include_router(devices.router, prefix="/api/devices", tags=["设备管理"])
app.include_router(data.router, prefix="/api/data", tags=["数据查询"])
app.include_router(ws.router, prefix="/api/ws", tags=["实时推送"])
app.include_router(monitor.router, prefix="/api/monitor", tags=["系统监控"])


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "工业物联网设备监控平台",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "运行中"
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": settings.APP_NAME
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
