import json
import os


class Config:
    def __init__(self, path="config.json"):
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到配置文件: {os.path.abspath(path)}")
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    def get(self, path_str, default=None):
        """按点号路径访问嵌套配置，不存在时返回 default"""
        keys = path_str.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val
