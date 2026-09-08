"""测试共享夹具与工具(integration/e2e 依赖外部服务的探测)。"""

from __future__ import annotations

import socket
from urllib.parse import urlparse


def milvus_reachable(uri: str, timeout: float = 2.0) -> bool:
    """Milvus 在线 TCP 快速探测:gRPC 连接失败的重试放大很慢,先探端口再构造客户端。"""
    parsed = urlparse(uri)
    try:
        with socket.create_connection((parsed.hostname or "", parsed.port or 19530), timeout=timeout):
            return True
    except OSError:
        return False
