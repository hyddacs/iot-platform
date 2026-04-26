"""
WebSocket连接管理器
"""
import json
import asyncio
import logging
from concurrent.futures import Future
from typing import Dict, Iterable, Optional, Set
from datetime import datetime
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """WebSocket连接管理器"""
    
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: Optional[asyncio.AbstractEventLoop]):
        """记录应用主事件循环，便于其他线程安全调度广播。"""
        self._loop = loop
    
    async def connect(self, websocket: WebSocket, device_id: str = None):
        """建立WebSocket连接"""
        await websocket.accept()
        
        async with self.lock:
            if device_id not in self.active_connections:
                self.active_connections[device_id] = set()
            self.active_connections[device_id].add(websocket)
        
        logger.info(f"WebSocket连接建立: {device_id}")
    
    def disconnect(self, websocket: WebSocket):
        """断开WebSocket连接"""
        for device_id, connections in list(self.active_connections.items()):
            if websocket in connections:
                connections.remove(websocket)
                if not connections:
                    del self.active_connections[device_id]
                break

    def _get_target_connections(self, device_id: Optional[str] = None) -> Set[WebSocket]:
        """返回目标连接快照，避免广播过程中被原地修改。"""
        connections: Set[WebSocket] = set()

        if device_id in self.active_connections:
            connections.update(self.active_connections[device_id])

        if None in self.active_connections:
            connections.update(self.active_connections[None])

        return connections

    async def _broadcast_message(self, message: str, connections: Iterable[WebSocket]):
        connection_list = list(connections)
        if not connection_list:
            return

        async def send(connection: WebSocket):
            try:
                await asyncio.wait_for(connection.send_text(message), timeout=3)
                return None
            except Exception:
                return connection

        disconnected = [
            connection
            for connection in await asyncio.gather(*(send(connection) for connection in connection_list))
            if connection is not None
        ]

        for connection in disconnected:
            self.disconnect(connection)

    def _dispatch(self, coroutine) -> Optional[Future]:
        """在主事件循环中调度广播，供后台线程调用。"""
        if self._loop is None or self._loop.is_closed():
            logger.warning("WebSocket主事件循环不可用，跳过广播")
            return None

        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)
    
    async def broadcast_device_data(self, device_id: str, data: dict):
        """广播设备数据"""
        message = json.dumps({
            "type": "device_data",
            "device_id": device_id,
            "data": data,
            "timestamp": datetime.now().isoformat()
        })
        await self._broadcast_message(message, self._get_target_connections(device_id))
    
    async def broadcast_alert(self, alert: dict):
        """广播告警"""
        message = json.dumps({
            "type": "alert",
            "alert": alert,
            "timestamp": datetime.now().isoformat()
        })
        connections = {
            connection
            for group in self.active_connections.values()
            for connection in group
        }
        await self._broadcast_message(message, connections)
    
    async def broadcast_device_status(self, device_id: str, is_online: bool):
        """广播设备状态变化"""
        message = json.dumps({
            "type": "device_status",
            "device_id": device_id,
            "online": is_online,
            "timestamp": datetime.now().isoformat()
        })
        connections = {
            connection
            for group in self.active_connections.values()
            for connection in group
        }
        await self._broadcast_message(message, connections)

    def dispatch_device_data(self, device_id: str, data: dict) -> Optional[Future]:
        return self._dispatch(self.broadcast_device_data(device_id, data))

    def dispatch_alert(self, alert: dict) -> Optional[Future]:
        return self._dispatch(self.broadcast_alert(alert))

    def dispatch_device_status(self, device_id: str, is_online: bool) -> Optional[Future]:
        return self._dispatch(self.broadcast_device_status(device_id, is_online))


# 全局实例
websocket_manager = WebSocketManager()
