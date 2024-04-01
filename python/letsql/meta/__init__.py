"""Initialize Ibis module."""

from __future__ import annotations


from letsql.meta.backend import Backend
# from letsql.meta.operations import DeferredCachedTable

# from ibis.expr.types.core import Expr
#
#
# def _find_backends(self) -> tuple[list[BaseBackend], bool]:
#     """Return the possible backends for an expression.
#
#     Returns
#     -------
#     list[BaseBackend]
#         A list of the backends found.
#     """
#     backends = set()
#     has_unbound = False
#     node_types = (ops.DatabaseTable, ops.SQLQueryResult, ops.UnboundTable, DeferredCachedTable)
#     for table in self.op().find(node_types):
#         if isinstance(table, ops.UnboundTable):
#             has_unbound = True
#         else:
#             backends.add(table.source)
#
#     return list(backends), has_unbound
#
#
# Expr._find_backends = _find_backends

backend = Backend()
backend.register_options()

add_connection = backend.add_connection
connect = backend.do_connect
table = backend.table
