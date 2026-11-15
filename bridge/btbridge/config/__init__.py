"""Persistent appliance configuration (settings + separately stored secrets)."""
from .schema import SETTINGS, SettingError, validate
from .store import ConfigStore

__all__ = ["ConfigStore", "SETTINGS", "SettingError", "validate"]
