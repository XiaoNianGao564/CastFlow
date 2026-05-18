"""chromadb 1.5+ 兼容补丁。

观察到的真实 bug：
    AttributeError: 'RustBindingsAPI' object has no attribute 'bindings'
    File ".../chromadb/api/rust.py", line 131, in stop  ->  del self.bindings

触发场景：同进程内反复 PersistentClient(path=...)。当上一次 init 失败 / 半成品
系统残留在 SharedSystemClient 缓存中，新 init 调 _release_system → stop()，
而 stop() 假设 bindings 属性存在，结果 AttributeError 把整个图拉崩。

我们做三件事让它"自愈"：
  1. monkey-patch RustBindingsAPI.stop 让它幂等（缺 bindings 也不报错）
  2. 提供 clear_shared_system_cache() 主动清缓存
  3. 提供 safe_persistent_client() 包装：先清缓存 → init，失败一次自动重试

只在 import 此模块时一次性 patch。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PATCHED = False


def _patch_stop() -> None:
    """让 RustBindingsAPI.stop / 同名 api 的 stop 在缺属性时幂等。"""
    global _PATCHED
    if _PATCHED:
        return
    try:
        import chromadb.api.rust as _rust_mod  # type: ignore

        cls = getattr(_rust_mod, "RustBindingsAPI", None)
        if cls is None:
            return
        original = cls.stop

        def _safe_stop(self):  # type: ignore
            try:
                return original(self)
            except AttributeError:
                # bindings 没被赋值（init 失败）→ 静默吞掉
                return None

        cls.stop = _safe_stop  # type: ignore[method-assign]
        _PATCHED = True
    except Exception as e:  # noqa: BLE001
        logger.debug("chroma_compat: patch_stop skipped (%s)", e)


def clear_shared_system_cache() -> None:
    """清掉 chromadb 内部 SharedSystemClient 的进程级缓存（避免脏 system 复用）。"""
    try:
        from chromadb.api.shared_system_client import SharedSystemClient  # type: ignore

        # 不同 chromadb 版本属性名拼写不同（1.5.x 有 typo），都试一遍
        for attr in (
            "_identifier_to_system",
            "_identifer_to_system",
            "_identifier_to_systems",
        ):
            cache = getattr(SharedSystemClient, attr, None)
            if isinstance(cache, dict):
                cache.clear()
    except Exception as e:  # noqa: BLE001
        logger.debug("chroma_compat: clear_cache skipped (%s)", e)


def _ensure_tenant_and_db(path: str) -> bool:
    """显式 ensure default_tenant + default_database 存在。
    chromadb 1.5.x 在某些 import 顺序 / 进程状态下 PersistentClient 会
    跳过 tenant bootstrap，直接抛 'Could not connect to tenant default_tenant'。
    """
    try:
        import chromadb  # type: ignore
        from chromadb.config import (  # type: ignore
            DEFAULT_DATABASE,
            DEFAULT_TENANT,
            Settings,
        )

        admin = chromadb.AdminClient(
            Settings(is_persistent=True, persist_directory=path)
        )
        try:
            admin.get_tenant(DEFAULT_TENANT)
        except Exception:
            try:
                admin.create_tenant(DEFAULT_TENANT)
            except Exception:
                pass
        try:
            admin.get_database(DEFAULT_DATABASE, tenant=DEFAULT_TENANT)
        except Exception:
            try:
                admin.create_database(DEFAULT_DATABASE, tenant=DEFAULT_TENANT)
            except Exception:
                pass
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("chroma_compat: ensure_tenant skipped (%s)", e)
        return False


def safe_http_client(host: str, port: int):
    """构造 chromadb HttpClient（连 Docker 部署的 chroma server）。

    优先于 PersistentClient——HttpClient 在多线程/多 case 场景下稳定，
    避开 chromadb 1.5+ 的嵌入式 RustBindingsAPI 兼容性问题。
    """
    _patch_stop()
    import chromadb  # type: ignore

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            if attempt > 0:
                clear_shared_system_cache()
            return chromadb.HttpClient(host=host, port=int(port))
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "chroma_compat: HttpClient init failed on attempt %d: %s",
                attempt + 1,
                e,
            )
            continue

    print(f"[memory] chromadb HttpClient failed after retries: {last_err}")
    return None


def safe_persistent_client(path: str | Path):
    """构造 chromadb PersistentClient。

    自动处理 chromadb 1.5+ 的常见兼容 bug：
      1. RustBindingsAPI 缺 bindings 属性（_patch_stop 已修）
      2. "Could not connect to tenant default_tenant"（用 AdminClient 显式
         ensure tenant + database 后重试）
      3. 路径解析差异（统一用绝对路径，避免不同线程 cwd 不一致）

    Returns:
        chromadb client 或 None（彻底失败时）。
    """
    _patch_stop()
    import chromadb  # type: ignore

    abs_path = str(Path(path).resolve())
    Path(abs_path).mkdir(parents=True, exist_ok=True)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            if attempt > 0:
                clear_shared_system_cache()
            if attempt == 1:
                _ensure_tenant_and_db(abs_path)
            return chromadb.PersistentClient(path=abs_path)
        except (AttributeError, KeyError, RuntimeError, ValueError) as e:
            last_err = e
            msg = str(e).lower()
            logger.warning(
                "chroma_compat: PersistentClient init failed on attempt %d: %s",
                attempt + 1,
                e,
            )
            if "tenant" in msg or "database" in msg:
                _ensure_tenant_and_db(abs_path)
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            logger.warning(
                "chroma_compat: PersistentClient init failed on attempt %d: %s",
                attempt + 1,
                e,
            )
            if "tenant" in msg or "database" in msg:
                _ensure_tenant_and_db(abs_path)
                clear_shared_system_cache()
                continue
            break

    print(f"[memory] chromadb init failed after retries: {last_err}")
    return None



# Apply patch at import time so any code path using raw chromadb still benefits.
_patch_stop()
