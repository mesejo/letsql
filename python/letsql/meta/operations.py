from typing import Any

from ibis.expr.operations.relations import PhysicalTable
from ibis.expr.schema import Schema
from public import public


@public
class DeferredCachedTable(PhysicalTable):
    schema: Schema
    source: Any
    expr: Any
    name: str
