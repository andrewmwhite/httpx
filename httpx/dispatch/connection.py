import functools
import typing

from ..concurrency import AsyncioBackend
from ..config import (
    DEFAULT_TIMEOUT_CONFIG,
    CertTypes,
    SSLConfig,
    TimeoutConfig,
    TimeoutTypes,
    VerifyTypes,
)
from ..interfaces import AsyncDispatcher, ConcurrencyBackend, Protocol
from ..models import AsyncRequest, AsyncResponse, Origin
from .http2 import HTTP2Connection
from .http11 import HTTP11Connection

# Callback signature: async def callback(conn: HTTPConnection) -> None
ReleaseCallback = typing.Callable[["HTTPConnection"], typing.Awaitable[None]]


class HTTPConnection(AsyncDispatcher):
    def __init__(
        self,
        origin: typing.Union[str, Origin],
        verify: VerifyTypes = True,
        cert: CertTypes = None,
        timeout: TimeoutTypes = DEFAULT_TIMEOUT_CONFIG,
        backend: ConcurrencyBackend = None,
        release_func: typing.Optional[ReleaseCallback] = None,
    ):
        self.origin = Origin(origin) if isinstance(origin, str) else origin
        self.ssl = SSLConfig(cert=cert, verify=verify)
        self.timeout = TimeoutConfig(timeout)
        self.backend = AsyncioBackend() if backend is None else backend
        self.release_func = release_func
        self.h11_connection = None  # type: typing.Optional[HTTP11Connection]
        self.h2_connection = None  # type: typing.Optional[HTTP2Connection]

    async def send(
        self,
        request: AsyncRequest,
        verify: VerifyTypes = None,
        cert: CertTypes = None,
        timeout: TimeoutTypes = None,
    ) -> AsyncResponse:
        if self.h11_connection is None and self.h2_connection is None:
            await self.connect(verify=verify, cert=cert, timeout=timeout)

        if self.h2_connection is not None:
            response = await self.h2_connection.send(request, timeout=timeout)
        else:
            assert self.h11_connection is not None
            response = await self.h11_connection.send(request, timeout=timeout)

        return response

    async def connect(
        self,
        verify: VerifyTypes = None,
        cert: CertTypes = None,
        timeout: TimeoutTypes = None,
    ) -> None:
        ssl = self.ssl.with_overrides(verify=verify, cert=cert)
        timeout = self.timeout if timeout is None else TimeoutConfig(timeout)

        host = self.origin.host
        port = self.origin.port
        ssl_context = await ssl.load_ssl_context() if self.origin.is_ssl else None

        if self.release_func is None:
            on_release = None
        else:
            on_release = functools.partial(self.release_func, self)

        reader, writer, protocol = await self.backend.connect(
            host, port, ssl_context, timeout
        )
        if protocol == Protocol.HTTP_2:
            self.h2_connection = HTTP2Connection(
                reader, writer, self.backend, on_release=on_release
            )
        else:
            self.h11_connection = HTTP11Connection(
                reader, writer, self.backend, on_release=on_release
            )

    async def close(self) -> None:
        if self.h2_connection is not None:
            await self.h2_connection.close()
        elif self.h11_connection is not None:
            await self.h11_connection.close()

    @property
    def is_http2(self) -> bool:
        return self.h2_connection is not None

    @property
    def is_closed(self) -> bool:
        if self.h2_connection is not None:
            return self.h2_connection.is_closed
        else:
            assert self.h11_connection is not None
            return self.h11_connection.is_closed

    def is_connection_dropped(self) -> bool:
        if self.h2_connection is not None:
            return self.h2_connection.is_connection_dropped()
        else:
            assert self.h11_connection is not None
            return self.h11_connection.is_connection_dropped()
