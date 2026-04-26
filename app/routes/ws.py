"""
WebSocket路由
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
import json
from datetime import datetime
from app.websocket_manager import websocket_manager

router = APIRouter()


@router.websocket("")
async def websocket_endpoint(
    websocket: WebSocket,
    device_id: Optional[str] = Query(None)
):
    """WebSocket实时推送"""
    await websocket_manager.connect(websocket, device_id)
    
    try:
        await websocket.send_json({
            "type": "connected",
            "message": "连接成功",
            "device_id": device_id,
            "timestamp": datetime.now().isoformat()
        })
        
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                if message.get("type") == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": datetime.now().isoformat()
                    })
            except json.JSONDecodeError:
                pass
                
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
    except Exception:
        websocket_manager.disconnect(websocket)
