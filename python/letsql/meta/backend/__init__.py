from __future__ import annotations

import abc
import functools
from typing import TYPE_CHECKING, Any

import pyarrow_hotfix  # noqa: F401
from ibis import BaseBackend
from ibis.backends.datafusion import Backend as DataFusionBackend
from ibis.common.exceptions import IbisError
from ibis.expr import types as ir
from ibis.util import gen_name


try:
    from datafusion import ExecutionContext as SessionContext
except ImportError:
    from datafusion import SessionContext

try:
    from datafusion import SessionConfig
except ImportError:
    SessionConfig = None

if TYPE_CHECKING:
    pass


class CanListConnections(abc.ABC):

    @abc.abstractmethod
    def list_connections(
            self, like: str | None = None,
    ) -> list[BaseBackend]:
        pass


class CanCreateConnections(CanListConnections):
    @abc.abstractmethod
    def add_connection(
        self, connection: BaseBackend, name: str | None = None,
    ) -> None:
        """Add a connection named `name`.
        """

    @abc.abstractmethod
    def drop_connection(
        self, name: str,
    ) -> None:
        """Drop the connection with `name`.
        """


class Backend(DataFusionBackend, CanCreateConnections):

    connections = {}
    sources = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def add_connection(self, connection: BaseBackend, name: str | None = None) -> None:
        self.connections[connection.name] = connection

    def drop_connection(self, name: str) -> None:
        self.connections.pop(name)

    def list_connections(self, like: str | None = None) -> list[BaseBackend]:
        return list(self.connections.values())

    def execute(self, expr: ir.Expr, **kwargs: Any):

        self._register_deferred_cached_tables(expr)

        name = self._get_source(expr)

        if name == "datafusion":
            return super().execute(expr, **kwargs)

        backend = self.connections[name]

        return backend.execute(expr, **kwargs)

    def table(
        self,
        name: str,
        schema: str | None = None,
        database: tuple[str, str] | str | None = None,
    ) -> ir.Table:

        backends = list(self.connections.values())
        backends.append(super())

        for backend in backends:
            try:
                t = backend.table(name, schema=schema)
                original = t.op().source
                override = t.op().copy(source=self)
                self.sources[override] = original
                return override.to_expr()
            except IbisError:
                continue
        else:
            if self.connections:
                raise IbisError(f"Table not found: {name!r}")

    def list_tables(
        self,
        like: str | None = None,
        database: str | None = None,
    ) -> list[str]:

        backends = list(self.connections.values())
        backends.append(super())

        return [t for backend in backends for t in backend.list_tables(like=like)]

    def _get_source(self, expr: ir.Expr):
        origin = expr.op()
        while hasattr(origin, "parent"):
            origin = getattr(origin, "parent")

        if hasattr(origin, "table"):
            origin = origin.table

        source = self.sources.get(origin, self)
        return source.name

    def _load_into_cache(self, name, expr):
        source_name = self._get_source(expr)
        backend = self.connections[source_name]
        temp_table = backend.to_pyarrow(expr)
        super().create_table(name, temp_table, temp=True)

