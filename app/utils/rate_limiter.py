"""
接口限流器
"""
import time
import threading
from collections import defaultdict
from fastapi import HTTPException, status


class RateLimiter:
    """简单的内存限流器"""
    
    def __init__(self, max_requests: int, time_window: int):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = defaultdict(list)
        self.lock = threading.Lock()
    
    def check_rate_limit(self, client_key: str) -> bool:
        """检查是否超过频率限制"""
        with self.lock:
            current_time = time.time()
            
            if client_key in self.requests:
                self.requests[client_key] = [
                    req_time for req_time in self.requests[client_key]
                    if current_time - req_time < self.time_window
                ]
            
            if len(self.requests.get(client_key, [])) >= self.max_requests:
                return False
            
            self.requests[client_key].append(current_time)
            return True


_limiters = {}


def rate_limit_middleware(client_key: str, max_requests: int = 100, time_window: int = 60):
    """限流中间件"""
    limiter_key = (max_requests, time_window)
    limiter = _limiters.get(limiter_key)
    if limiter is None:
        limiter = RateLimiter(max_requests, time_window)
        _limiters[limiter_key] = limiter

    if not limiter.check_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试"
        )
    return True
