import os
import random
import logging
from typing import List, Optional
from pathlib import Path
from app.config import BASE_DIR

logger = logging.getLogger(__name__)

PROXY_FILE_PATH = BASE_DIR / "proxies.txt"

class ProxyManager:
    """
    Quản lý danh sách Proxy xoay vòng (HTTP/HTTPS/SOCKS5) chuẩn AIRead.
    Hỗ trợ đọc từ proxies.txt, biến môi trường PROXY_LIST, hoặc config.
    """
    _instance = None
    _proxies: List[str] = []
    _index: int = 0

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.reload_proxies()

    def reload_proxies(self) -> int:
        """Đọc và nạp danh sách proxy từ proxies.txt hoặc biến môi trường"""
        proxies_set = set()

        # 1. Đọc từ file proxies.txt ở thư mục gốc
        if PROXY_FILE_PATH.exists():
            try:
                with open(PROXY_FILE_PATH, "r", encoding="utf-8") as f:
                    for line in f:
                        p = line.strip()
                        if p and not p.startswith("#"):
                            proxies_set.add(self._format_proxy(p))
            except Exception as e:
                logger.error(f"[ProxyManager] Lỗi đọc file proxies.txt: {e}")

        # 2. Đọc từ biến môi trường PROXIES hoặc PROXY_LIST (nếu có, phân cách bởi dấu phẩy hoặc chấm phẩy)
        env_proxies = os.getenv("PROXY_LIST", os.getenv("PROXIES", "")).strip()
        if env_proxies:
            for p in env_proxies.replace(";", ",").split(","):
                p = p.strip()
                if p:
                    proxies_set.add(self._format_proxy(p))

        self._proxies = list(proxies_set)
        self._index = 0
        
        if self._proxies:
            logger.info(f"[ProxyManager] Đã nạp thành công {len(self._proxies)} Proxies xoay vòng!")
        return len(self._proxies)

    def _format_proxy(self, p: str) -> str:
        """Chuẩn hóa proxy về dạng protocol://ip:port hoặc protocol://user:pass@ip:port"""
        p = p.strip()
        if not (p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://") or p.startswith("socks4://")):
            return f"http://{p}"
        return p

    def get_proxy(self, randomize: bool = True) -> Optional[str]:
        """Lấy 1 proxy xoay vòng hoặc ngẫu nhiên. Trả về None nếu không cấu hình proxy."""
        if not self._proxies:
            return None
        
        if randomize:
            return random.choice(self._proxies)
        
        # Round-robin
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index = (self._index + 1) % len(self._proxies)
        return proxy

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    @property
    def count(self) -> int:
        return len(self._proxies)

proxy_manager = ProxyManager.get_instance()
