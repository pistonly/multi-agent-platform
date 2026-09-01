"""T28：MAPClient 连接级重试。

transport 未显式指定时默认创建 ``httpx.HTTPTransport(retries=1)``——
建连失败（连接拒绝/瞬时网络错误）自动重试一次；本地地址保持
trust_env=False（忽略环境代理），远端地址沿用环境代理配置。
调用方显式传 transport 时代理与重试完全由其自理，不包装、不代管关闭。
"""

import httpx
from map_client import MAPClient


def test_default_transport_has_connection_retries():
    with MAPClient("http://localhost:18400", "tok") as client:
        transport = client._http._transport
        assert isinstance(transport, httpx.HTTPTransport)
        assert transport._pool._retries == 1


def test_retries_zero_disables_connection_retry():
    with MAPClient("http://localhost:18400", "tok", retries=0) as client:
        assert client._http._transport._pool._retries == 0


def test_local_url_ignores_env_proxy():
    with MAPClient("http://127.0.0.1:18400", "tok") as client:
        assert client._http._trust_env is False


def test_remote_url_honors_env_proxy():
    with MAPClient("https://map.example.com", "tok") as client:
        assert client._http._trust_env is True


def test_caller_transport_not_wrapped():
    mock = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    client = MAPClient("http://localhost:18400", "tok", transport=mock)
    try:
        assert client._http._transport is mock
        assert client._transport is mock
    finally:
        client.close()


def test_caller_transport_survives_local_url():
    mock = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    client = MAPClient("http://localhost:18400", "tok", transport=mock)
    try:
        assert client._http._transport is mock
    finally:
        client.close()
