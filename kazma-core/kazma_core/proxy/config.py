"""One coherent, call-local proxy configuration; unavailable is not direct."""

from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from collections.abc import Mapping
import ipaddress

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

_snapshot: ContextVar[Mapping | None] = ContextVar('proxy_config_snapshot', default=None)


@contextmanager
def proxy_config_scope():
    if _snapshot.get() is not None:
        yield
        return
    from kazma_core.config_store import get_config_store

    try:
        config = get_config_store().get('proxy', {})
    except Exception:
        # Never include DB/vault exception text: it may contain credentials.
        raise AcquisitionError(FetchErrorCode.ROUTING,
            'Scraping proxy configuration could not be read. Repair the settings store; direct fallback was refused.',
            transient=True) from None
    if not isinstance(config, dict):
        raise AcquisitionError(FetchErrorCode.ROUTING,
            'Scraping proxy settings must be a mapping. Repair Proxy Provider settings; direct fallback was refused.')
    token = _snapshot.set(MappingProxyType(dict(config)))
    try:
        yield
    finally:
        _snapshot.reset(token)


def proxy_value(key, default=None):
    with proxy_config_scope():
        value = _snapshot.get().get(key)
        if value is not None and (
            (key in {'provider', 'username', 'password', 'host', 'network', 'country'} and not isinstance(value, str))
            or (key == 'port' and (isinstance(value, bool) or not isinstance(value, (str, int))))
            or (key == 'session_sticky' and not isinstance(value, bool))
        ):
            raise AcquisitionError(FetchErrorCode.ROUTING,
                f'Scraping proxy setting {key} has an invalid type. Repair Proxy Provider settings before retrying.')
        if isinstance(value, str) and value.startswith('vault://'):
            raise AcquisitionError(FetchErrorCode.ROUTING,
                f'Scraping proxy setting {key} has an unavailable vault secret. Unlock the vault before retrying.')
        if key == 'host' and isinstance(value, str) and value.strip():
            value = value.strip()
            if any(c in value for c in '/\\@?#;%') or any(c.isspace() or ord(c) < 32 for c in value):
                raise AcquisitionError(FetchErrorCode.ROUTING,
                    'Scraping proxy host must be a bare hostname or IP address, without URL credentials or a path.')
            if ':' in value and not value.startswith('['):
                try:
                    ipaddress.IPv6Address(value)
                except ValueError:
                    raise AcquisitionError(FetchErrorCode.ROUTING,
                        'Scraping proxy host must not include a scheme or port. Set the port separately.') from None
                value = f'[{value}]'
        return default if value is None else value
