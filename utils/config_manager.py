# config_manager.py
import json
import ctypes
import os

# 必须在所有 GUI 操作前调用
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass 

class Config:
    def __init__(self, path="config.json"):
        # 考虑到你可能在不同目录下运行，加个路径判断
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到配置文件: {os.path.abspath(path)}")
            
        with open(path, 'r', encoding='utf-8') as f:
            self._data = json.load(f)
        
        self.scale = self._data["display"]["scaling_factor"]

    def get(self, path_str):
        """通用获取方法：支持访问 json 的任意层级"""
        keys = path_str.split('.')
        val = self._data
        for k in keys:
            val = val[k]
        return val

    def get_coord(self, path_str):
        """
        专用坐标获取：自动加上 coords 前缀并应用 1.5x 缩放
        例如: get_coord("buttons.fold")
        """
        # 自动补全路径前缀
        full_path = f"coords.{path_str}"
        val = self.get(full_path)
        
        # 点坐标 [x, y]
        if isinstance(val, list) and len(val) == 2:
            return (int(val[0] * self.scale), int(val[1] * self.scale))
        
        # 区域坐标 [x, y, w, h]
        elif isinstance(val, list) and len(val) == 4:
            return (
                int(val[0] * self.scale), 
                int(val[1] * self.scale), 
                int(val[2] * self.scale), 
                int(val[3] * self.scale)
            )
        return val

    def get_color(self, name):
        """获取颜色 RGB 元组"""
        return tuple(self._data["colors"][name])