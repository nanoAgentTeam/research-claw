"""
用户配置管理模块

提供用户级别的配置管理。
使用单例模式确保全局只有一个实例。
"""

import json
import os
from typing import List
from core.infra.config import Config


class UserConfig:
    """
    用户配置管理类（单例）
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UserConfig, cls).__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        self.config_path = Config.USER_CONFIG_FILE
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
        else:
            # 默认配置
            self.data = {
                "user_profile": "",
                "language": "ch",
                "workspace": ""
            }
            self.save()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    @property
    def user_profile(self) -> str:
        return self.data.get("user_profile", "")

    @user_profile.setter
    def user_profile(self, val: str) -> None:
        self.data["user_profile"] = val
        self.save()

    @property
    def language(self) -> str:
        return self.data.get("language", "ch")

    @language.setter
    def language(self, val: str) -> None:
        self.data["language"] = val
        self.save()
