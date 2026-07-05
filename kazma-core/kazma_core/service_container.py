"""Thread-safe Dependency Injection Service Container.

Provides registration, retrieval, and cleanup hooks for core framework services
and singletons, eliminating global module-level mutations and enabling safe
testing and concurrency.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ServiceContainer:
    """Thread-safe dependency injection and service locator container."""

    def __init__(self) -> None:
        self._services: dict[Type[Any] | str, Any] = {}
        self._factories: dict[Type[Any] | str, Callable[[], Any]] = {}
        self._lock = threading.RLock()

    def register(
        self,
        key: Type[T] | str,
        service: T | None = None,
        factory: Callable[[], T] | None = None,
    ) -> None:
        """Register a service instance or factory.

        Args:
            key: The type class or unique string key representing the service.
            service: An instantiated service instance.
            factory: A lazy callable returning the service instance when requested.
        """
        if service is None and factory is None:
            raise ValueError("Must provide either a service instance or a factory callable.")

        with self._lock:
            if service is not None:
                self._services[key] = service
                # Clear any factory if we are explicitly setting an instance
                if key in self._factories:
                    del self._factories[key]
                logger.debug("[ServiceContainer] Registered service instance for: %s", key)
            elif factory is not None:
                self._factories[key] = factory
                # Clear any cached instance to allow the factory to run on next get
                if key in self._services:
                    del self._services[key]
                logger.debug("[ServiceContainer] Registered service factory for: %s", key)

    def get(self, key: Type[T] | str) -> T:
        """Retrieve a service instance.

        If a factory is registered for this key, it is evaluated and cached.

        Raises:
            KeyError: If the service key is not registered.
        """
        with self._lock:
            if key in self._services:
                return self._services[key]

            if key in self._factories:
                instance = self._factories[key]()
                self._services[key] = instance
                logger.debug("[ServiceContainer] Evaluated factory and cached service for: %s", key)
                return instance

            raise KeyError(f"Service '{key}' is not registered in the container.")

    def has(self, key: Type[Any] | str) -> bool:
        """Check if a service or factory is registered for the given key."""
        with self._lock:
            return key in self._services or key in self._factories

    def remove(self, key: Type[Any] | str) -> None:
        """Remove a service instance and factory from the container."""
        with self._lock:
            if key in self._services:
                del self._services[key]
            if key in self._factories:
                del self._factories[key]
            logger.debug("[ServiceContainer] Removed service entry for: %s", key)

    def reset_all(self) -> None:
        """Clear all registered services and factories."""
        with self._lock:
            self._services.clear()
            self._factories.clear()
            logger.info("[ServiceContainer] Container reset — all services cleared")


# ── Global Container Singleton Access ─────────────────────────────────

_global_container: ServiceContainer | None = None
_global_lock = threading.Lock()


def get_container() -> ServiceContainer:
    """Return the global thread-safe ServiceContainer singleton."""
    global _global_container
    if _global_container is None:
        with _global_lock:
            if _global_container is None:
                _global_container = ServiceContainer()
    return _global_container


def reset_container() -> None:
    """Reset the global ServiceContainer, clearing all cached services."""
    if _global_container is not None:
        _global_container.reset_all()
