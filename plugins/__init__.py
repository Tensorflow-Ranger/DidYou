"""
Platform Plugin system - ABC and Registry.
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# pf:api_contract:plugins.platform_plugin_abc Abstract base class for platform plugins
class PlatformPlugin(ABC):
    """Abstract base class defining the platform plugin interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name (e.g. 'telegram', 'whatsapp', 'twilio')."""
        ...

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Channel name used in escalation config (e.g. 'telegram', 'whatsapp', 'sms', 'call')."""
        ...

    @abstractmethod
    def start(self) -> None:
        """Start plugin (may raise on invalid config)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Graceful shutdown."""
        ...

    @abstractmethod
    def send_message(self, target: str, message: str, **kwargs) -> Optional[str]:
        """Send message to target. Returns message ID or None on failure."""
        ...

    @property
    def is_running(self) -> bool:
        """Whether plugin is active. Override for custom logic."""
        return getattr(self, "_running", False)


# pf:api_contract:plugins.registry Plugin registry for managing active platform plugins
class PluginRegistry:
    """Registry for managing active platform plugins."""

    def __init__(self) -> None:
        self._plugins: Dict[str, PlatformPlugin] = {}

    # pf:requires:registry.plugin_name_unique Each plugin must have a unique name
    def register(self, plugin: PlatformPlugin) -> None:
        """Register a plugin. Raises ValueError if name already registered."""
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' already registered")
        self._plugins[plugin.name] = plugin

    # pf:ensures:registry.start_all_resilient Starts all plugins, logs errors, never crashes
    # pf:never:registry.never_crash_on_plugin_failure Never let one plugin failure prevent others
    def start_all(self) -> None:
        """Start all registered plugins. Logs errors, never crashes."""
        for name, plugin in self._plugins.items():
            try:
                plugin.start()
                logger.info(f"Plugin '{name}' started successfully")
            except Exception as e:
                logger.error(f"Plugin '{name}' failed to start: {e}")

    def stop_all(self) -> None:
        """Stop all registered plugins."""
        for name, plugin in self._plugins.items():
            try:
                plugin.stop()
                logger.info(f"Plugin '{name}' stopped")
            except Exception as e:
                logger.error(f"Plugin '{name}' failed to stop: {e}")

    # pf:ensures:registry.send_routes_correctly Routes to plugin by channel_name, returns bool
    # pf:never:registry.never_crash_on_send_failure Never raise from send() - return False
    def send(self, channel: str, target: str, message: str, **kwargs) -> bool:
        """
        Send a message via the plugin matching the given channel.
        Returns True on success, False if plugin not found or send fails.
        """
        try:
            for plugin in self._plugins.values():
                if plugin.channel_name == channel:
                    result = plugin.send_message(target, message, **kwargs)
                    return result is not None
            logger.warning(f"No plugin found for channel '{channel}'")
            return False
        except Exception as e:
            logger.error(f"Error sending via channel '{channel}': {e}")
            return False

    def get_plugin(self, name: str) -> Optional[PlatformPlugin]:
        """Get a plugin by name."""
        return self._plugins.get(name)

    def list_active(self) -> List[str]:
        """List names of all running plugins."""
        return [name for name, p in self._plugins.items() if p.is_running]
