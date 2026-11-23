"""UUIDv7 生成器。

布局：48 位毫秒时间戳 | 4 位版本(7) | 12 位序列 | 2 位变体 | 62 位随机数。
进程内用锁 + 序列号保证单调递增；系统时钟回拨时借用序列位而不是生成逆序 id。
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_SEQ_BITS = 12
_SEQ_MAX = (1 << _SEQ_BITS) - 1
_RAND_B_MASK = (1 << 62) - 1

_lock = threading.Lock()
_last_ms = 0
_last_seq = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compose(ms: int, seq: int, rand_b: int) -> uuid.UUID:
    value = (
        ((ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)
        | ((seq & _SEQ_MAX) << 64)
        | (0b10 << 62)
        | (rand_b & _RAND_B_MASK)
    )
    return uuid.UUID(int=value)


def new_id() -> uuid.UUID:
    """生成一个时间有序的 UUIDv7。"""
    global _last_ms, _last_seq

    rand_b = int.from_bytes(os.urandom(8), "big") & _RAND_B_MASK
    with _lock:
        ms = _now_ms()
        if ms > _last_ms:
            _last_ms, _last_seq = ms, 0
        else:
            # 同一毫秒内递增，或时钟回拨时沿用上次时间戳继续递增
            seq = _last_seq + 1
            if seq > _SEQ_MAX:
                _last_ms += 1
                seq = 0
            _last_seq = seq
            ms = _last_ms
        return _compose(ms, _last_seq, rand_b)


def new_id_str() -> str:
    return str(new_id())


def timestamp_ms(value: uuid.UUID) -> int:
    """取回 UUIDv7 内的毫秒时间戳。"""
    return value.int >> 80
