"""文件缓存：命中 / 串味 / 过期 / 清空（自检 [13]）"""

from data.cache import FileCache


def test_set_then_hit(tmp_path):
    cache = FileCache(tmp_path, ttl_seconds=60)
    cache.set("k1", {"a": 1})
    assert cache.get("k1") == {"a": 1}


def test_different_keys_do_not_mix(tmp_path):
    cache = FileCache(tmp_path, ttl_seconds=60)
    cache.set("k1", {"a": 1})
    assert cache.get("k2") is None


def test_expired_entry_misses(tmp_path):
    cache = FileCache(tmp_path, ttl_seconds=60)
    cache.set("k1", {"a": 1})
    assert FileCache(tmp_path, ttl_seconds=-1).get("k1") is None


def test_clear_returns_removed_count(tmp_path):
    cache = FileCache(tmp_path, ttl_seconds=60)
    cache.set("k1", {"a": 1})
    assert cache.clear() == 1
