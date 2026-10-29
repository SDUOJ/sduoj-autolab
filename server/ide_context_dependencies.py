"""Read-only native assignment RPC dependencies; independent of AI task storage."""
from contextlib import contextmanager
import os
import threading

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_factory = None
_factory_lock = threading.Lock()


def _get(url, **kwargs):
    # Internal credentials must never follow an ambient outbound proxy or .netrc.
    with requests.Session() as session:
        session.trust_env = False
        return session.get(url, allow_redirects=False, **kwargs)


def user_service_address():
    # This is operator configuration, never a caller-supplied URL.
    configured = os.environ.get('AUTOLAB_IDE_USER_SERVICE_ADDRESS', '').strip()
    if configured:
        from urllib.parse import urlsplit
        address = urlsplit('http://' + configured)
        if (address.username or address.password or not address.hostname
                or address.path or address.query or address.fragment or not address.port):
            raise ValueError('Expected a user-service host:port')
        return configured
    from const import NACOS_addr, NACOS_namespace
    response = _get('http://' + NACOS_addr + '/nacos/v1/ns/instance/list',
        params={'serviceName': 'DEFAULT_GROUP@@user-service', 'namespaceId': NACOS_namespace},
        timeout=(.3, .5))
    if response.status_code != 200:
        raise RuntimeError('User-service discovery unavailable')
    hosts = response.json().get('hosts', [])
    available = [row for row in hosts if row.get('healthy', True) and row.get('enabled', True)]
    if not available:
        raise RuntimeError('No healthy user-service')
    row = available[0]
    import ipaddress
    address = ipaddress.ip_address(row['ip'])
    port = row['port']
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('Invalid discovered service port')
    return ('[' + str(address) + ']' if address.version == 6 else str(address)) + ':' + str(port)


def fetch_actor(subject, fresh=True):
    """Always resolve current permissions, never trust Gateway role claims here."""
    from auth import validate_user_info
    from const import SDUOJ_TOKEN
    if not SDUOJ_TOKEN:
        raise RuntimeError('Internal credentials required')
    value = str(subject)
    if not value.isascii() or not value.isdecimal() or value.startswith('0'):
        raise ValueError('Invalid user ID')
    response = _get('http://' + user_service_address() + '/internal/user/runSubject',
        params={'userId': value}, headers={'sduoj-token': SDUOJ_TOKEN},
        timeout=(.5, 1))
    if response.status_code != 200:
        raise RuntimeError('Current user identity unavailable')
    actor = response.json()
    def java_id(item):
        if isinstance(item, str):
            if not item.isascii() or not item.isdecimal() or item.startswith('0') or len(item) > 19:
                raise ValueError('Invalid internal ID')
            return int(item)
        return item
    if isinstance(actor, dict):
        actor = {**actor, 'userId': java_id(actor.get('userId'))}
        if isinstance(actor.get('groups'), list):
            actor['groups'] = [java_id(item) for item in actor['groups']]
    actor = validate_user_info(actor)
    if str(actor['userId']) != value:
        raise ValueError('User identity mismatch')
    return actor


@contextmanager
def transaction():
    """No commit: authorization reads must not create AI tables or answer sheets."""
    global _factory
    if _factory is None:
        with _factory_lock:
            if _factory is None:
                from db import link
                _factory = sessionmaker(bind=create_engine(link, pool_pre_ping=True,
                    pool_recycle=1800, pool_size=4, max_overflow=4, pool_timeout=1), autoflush=False)
    session = _factory()
    try:
        yield session
    finally:
        try:
            session.rollback()
        finally:
            session.close()
