"""Kazma TUI — Adaptive refresh manager for optimal performance.

Features:
    - Dynamic refresh rate based on user activity
    - Debounced updates to prevent flickering
    - Background task management with cancellation
    - Resource-aware throttling
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Awaitable

from textual.reactive import reactive
from textual.widget import Widget

logger = logging.getLogger(__name__)


class AdaptiveRefresh:
    """Manages adaptive refresh rates based on activity level.
    
    Refreshes faster when user is interacting, slower when idle.
    """
    
    def __init__(
        self,
        base_interval: float = 2.0,
        active_interval: float = 0.5,
        idle_threshold: float = 5.0,
    ) -> None:
        self.base_interval = base_interval
        self.active_interval = active_interval
        self.idle_threshold = idle_threshold
        self._last_activity = asyncio.get_event_loop().time()
        self._is_user_active = False
    
    def record_activity(self) -> None:
        """Record user activity to trigger faster refresh."""
        self._last_activity = asyncio.get_event_loop().time()
        self._is_user_active = True
    
    def get_interval(self) -> float:
        """Get current refresh interval based on activity."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_activity
        
        if elapsed < self.idle_threshold:
            self._is_user_active = True
            return self.active_interval
        else:
            self._is_user_active = False
            return self.base_interval
    
    @property
    def is_user_active(self) -> bool:
        """Check if user is currently active."""
        now = asyncio.get_event_loop().time()
        return (now - self._last_activity) < self.idle_threshold


class Debouncer:
    """Debounces function calls to prevent rapid successive executions.
    
    Useful for preventing UI flickering during rapid data updates.
    """
    
    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay
        self._task: Optional[asyncio.Task] = None
        self._pending_args: tuple = ()
        self._pending_kwargs: dict = {}
    
    def debounce(self, func: Callable) -> Callable:
        """Decorator to debounce a function."""
        async def debounced(*args, **kwargs) -> None:
            self._pending_args = args
            self._pending_kwargs = kwargs
            
            if self._task and not self._task.done():
                self._task.cancel()
            
            async def execute() -> None:
                await asyncio.sleep(self.delay)
                try:
                    if asyncio.iscoroutinefunction(func):
                        await func(*self._pending_args, **self._pending_kwargs)
                    else:
                        func(*self._pending_args, **self._pending_kwargs)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"Debounced function error: {e}")
            
            self._task = asyncio.create_task(execute())
        
        return debounced


def debounce(delay: float = 0.3) -> Callable:
    """Decorator factory for debouncing functions.
    
    Usage:
        @debounce(0.3)
        async def update_ui(self):
            ...
    """
    def decorator(func: Callable) -> Callable:
        debouncer = Debouncer(delay)
        return debouncer.debounce(func)
    return decorator


class TaskManager:
    """Manages background tasks with proper lifecycle handling.
    
    Features:
        - Automatic cleanup on shutdown
        - Error handling with retry logic
        - Cancellation support
    """
    
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._running = False
    
    def start(self) -> None:
        """Start the task manager."""
        self._running = True
    
    def stop(self) -> None:
        """Stop all managed tasks."""
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
    
    async def spawn(
        self,
        coro: Awaitable,
        name: str = "background_task",
        on_error: Optional[Callable[[Exception], None]] = None,
        retry_count: int = 0,
    ) -> asyncio.Task:
        """Spawn a managed background task.
        
        Args:
            coro: Coroutine to execute
            name: Task name for logging
            on_error: Optional error handler callback
            retry_count: Number of retries on failure
        
        Returns:
            The created asyncio.Task
        """
        async def wrapper() -> None:
            attempts = 0
            while self._running:
                try:
                    await coro
                    break  # Success, exit loop
                except asyncio.CancelledError:
                    logger.debug(f"Task '{name}' cancelled")
                    raise
                except Exception as e:
                    attempts += 1
                    if on_error:
                        on_error(e)
                    logger.warning(f"Task '{name}' error (attempt {attempts}): {e}")
                    
                    if attempts > retry_count:
                        logger.error(f"Task '{name}' failed after {attempts} attempts")
                        break
                    
                    # Exponential backoff
                    await asyncio.sleep(min(2 ** attempts, 10))
        
        task = asyncio.create_task(wrapper(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
    
    def create_refresh_task(
        self,
        refresh_func: Callable[[], Awaitable[None]],
        interval: float = 2.0,
        name: str = "refresh",
    ) -> asyncio.Task:
        """Create a periodic refresh task.
        
        Args:
            refresh_func: Async function to call periodically
            interval: Time between refreshes in seconds
            name: Task name for logging
        
        Returns:
            The created asyncio.Task
        """
        async def periodic_refresh() -> None:
            while self._running:
                try:
                    await refresh_func()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Refresh error in '{name}': {e}")
                
                await asyncio.sleep(interval)
        
        task = asyncio.create_task(periodic_refresh(), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
    
    @property
    def task_count(self) -> int:
        """Get number of active tasks."""
        return len(self._tasks)
    
    def clear_done(self) -> None:
        """Remove completed tasks from tracking."""
        self._tasks = {t for t in self._tasks if not t.done()}


class ResourceMonitor:
    """Monitors system resources and adjusts behavior accordingly.
    
    Features:
        - CPU usage tracking
        - Memory usage tracking
        - Throttling recommendations
    """
    
    def __init__(
        self,
        cpu_threshold: float = 80.0,
        memory_threshold: float = 80.0,
    ) -> None:
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold
        self._throttled = False
    
    def check_resources(self) -> bool:
        """Check if resources are within acceptable limits.
        
        Returns:
            True if resources are OK, False if throttling recommended
        """
        try:
            import psutil
            
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory_percent = psutil.virtual_memory().percent
            
            self._throttled = (
                cpu_percent > self.cpu_threshold or
                memory_percent > self.memory_threshold
            )
            
            if self._throttled:
                logger.debug(
                    f"Resource throttling: CPU={cpu_percent:.1f}%, "
                    f"Memory={memory_percent:.1f}%"
                )
            
            return not self._throttled
        except ImportError:
            # psutil not available, assume resources are OK
            return True
        except Exception as e:
            logger.debug(f"Resource check error: {e}")
            return True
    
    @property
    def is_throttled(self) -> bool:
        """Check if throttling is recommended."""
        return self._throttled
    
    def get_recommended_interval(self, base_interval: float) -> float:
        """Get recommended refresh interval based on resources.
        
        Args:
            base_interval: Normal refresh interval
        
        Returns:
            Adjusted interval (longer if throttled)
        """
        if self._throttled:
            return base_interval * 2  # Double interval when throttled
        return base_interval


class PerformanceManager:
    """Centralized performance management combining all features.
    
    Usage:
        perf_mgr = PerformanceManager()
        perf_mgr.start()
        
        # Use in widget
        @perf_mgr.debounce(0.3)
        async def update_display(self):
            ...
    """
    
    def __init__(
        self,
        base_refresh_interval: float = 2.0,
        active_refresh_interval: float = 0.5,
        debounce_delay: float = 0.3,
    ) -> None:
        self.adaptive = AdaptiveRefresh(
            base_interval=base_refresh_interval,
            active_interval=active_refresh_interval,
        )
        self.task_manager = TaskManager()
        self.resource_monitor = ResourceMonitor()
        self.debouncer = Debouncer(debounce_delay)
        self._running = False
    
    def start(self) -> None:
        """Start the performance manager."""
        self._running = True
        self.task_manager.start()
    
    def stop(self) -> None:
        """Stop the performance manager."""
        self._running = False
        self.task_manager.stop()
    
    def record_activity(self) -> None:
        """Record user activity."""
        self.adaptive.record_activity()
    
    def get_refresh_interval(self) -> float:
        """Get current recommended refresh interval."""
        base = self.adaptive.get_interval()
        return self.resource_monitor.get_recommended_interval(base)
    
    def debounce(self, func):
        """Debounce a function."""
        return self.debouncer.debounce(func)
    
    def create_refresh_task(
        self,
        refresh_func: Callable[[], Awaitable[None]],
        name: str = "refresh",
    ) -> asyncio.Task:
        """Create an adaptive refresh task."""
        async def adaptive_refresh() -> None:
            while self._running:
                try:
                    await refresh_func()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Adaptive refresh error: {e}")
                
                # Get dynamic interval
                interval = self.get_refresh_interval()
                await asyncio.sleep(interval)
        
        return self.task_manager.spawn(
            adaptive_refresh(),
            name=name,
        )
