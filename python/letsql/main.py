from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import ibis
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyarrow as pa
import pyarrow.dataset as ds
from ibis.backends.base import BaseBackend, CanCreateSchema
from ibis.backends.base.sqlglot import STAR
from sqlglot import exp, transforms
from sqlglot.dialects import Postgres

from letsql.compiler.core import translate

if TYPE_CHECKING:
    import pandas as pd
    from collections.abc import Mapping

from ._internal import SessionContext, SessionConfig

import ibis.expr.schema as sch

import sqlglot as sg

_exclude_exp = (exp.Pow, exp.ArrayContains)


class LetSql(Postgres):
    class Generator(Postgres.Generator):
        TRANSFORMS = {
            exp: trans
            for exp, trans in Postgres.Generator.TRANSFORMS.items()
            if exp not in _exclude_exp
        } | {
            exp.Select: transforms.preprocess(
                [
                    transforms.eliminate_qualify,
                ]
            ),
        }


class Backend(BaseBackend, CanCreateSchema):
    name = "letsql"
    dialect = "letsql"
    builder = None
    supports_in_memory_tables = True
    supports_arrays = True
    supports_python_udfs = False

    @property
    def version(self):
        return "0.1.1"

    def do_connect(
        self,
    ) -> None:
        df_config = SessionConfig(
            {"datafusion.sql_parser.dialect": "PostgreSQL"}
        ).with_information_schema(True)
        self.con = SessionContext(df_config)

    def list_tables(
        self, like: str | None = None, database: str | None = None
    ) -> list[str]:
        """List the available tables."""
        return self._filter_with_like(self.con.tables(), like)

    def table(self, name: str, schema: sch.Schema | None = None) -> ir.Table:
        """Get an ibis expression representing a DataFusion table.

        Parameters
        ----------
        name
            The name of the table to retrieve
        schema
            An optional schema for the table

        Returns
        -------
        Table
            A table expression
        """
        catalog = self.con.catalog()
        database = catalog.database()
        table = database.table(name)
        schema = sch.schema(table.schema)
        return ops.DatabaseTable(name, schema, self).to_expr()

    def register(
        self,
        source: str | Path | pa.Table | pa.RecordBatch | pa.Dataset | pd.DataFrame,
        table_name: str | None = None,
        **kwargs: Any,
    ):
        """Register a data set with `table_name` located at `source`.

        Parameters
        ----------
        source
            The data source(s). Maybe a path to a file or directory of
            parquet/csv files, a pandas dataframe, or a pyarrow table, dataset
            or record batch.
        table_name
            The name of the table
        """

        if isinstance(source, (str, Path)):
            first = str(source)

        if first.startswith(("parquet://", "parq://")) or first.endswith(
            ("parq", "parquet")
        ):
            self.con.deregister_table(table_name)
            self.con.register_parquet(table_name, first, file_extension=".parquet")
            return self.table(table_name)
        elif first.startswith(("csv://", "txt://")) or first.endswith(
            ("csv", "tsv", "txt")
        ):
            self.con.deregister_table(table_name)
            self.con.register_csv(table_name, first, **kwargs)
            return self.table(table_name)
        elif first.endswith("csv.gz"):
            self.con.deregister_table(table_name)
            self.con.register_csv(
                table_name, first, file_extension="gz", file_compression_type="gzip"
            )
            return self.table(table_name)
        else:
            self._register_failure()
            return None

    def _register_failure(self):
        import inspect

        msg = ", ".join(
            m[0] for m in inspect.getmembers(self) if m[0].startswith("read_")
        )
        raise ValueError(
            f"Cannot infer appropriate read function for input, "
            f"please call one of {msg} directly"
        )

    def _define_udf_translation_rules(self, expr):
        if self.supports_python_udfs:
            raise NotImplementedError(self.name)

    def _register_in_memory_table(self, op: ops.InMemoryTable) -> None:
        name = op.name
        schema = op.schema

        self.con.deregister_table(name)
        if batches := op.data.to_pyarrow(schema).to_batches():
            self.con.register_record_batches(name, [batches])
        else:
            empty_dataset = ds.dataset([], schema=schema.to_pyarrow())
            self.con.register_dataset(name=name, dataset=empty_dataset)

    def _register_in_memory_tables(self, expr: ir.Expr) -> None:
        if self.supports_in_memory_tables:
            for memtable in expr.op().find(ops.InMemoryTable):
                self._register_in_memory_table(memtable)

    def create_table(self, *_, **__) -> ir.Table:
        raise NotImplementedError(self.name)

    def create_view(self, *_, **__) -> ir.Table:
        raise NotImplementedError(self.name)

    def drop_table(self, *_, **__) -> ir.Table:
        raise NotImplementedError(self.name)

    def drop_view(self, *_, **__) -> ir.Table:
        raise NotImplementedError(self.name)

    @property
    def current_schema(self) -> str:
        return NotImplementedError()

    def list_schemas(
        self, like: str | None = None, database: str | None = None
    ) -> list[str]:
        return self._filter_with_like(
            self.con.catalog(
                database if database is not None else "datafusion"
            ).names(),
            like=like,
        )

    def create_schema(
        self, name: str, database: str | None = None, force: bool = False
    ) -> None:
        # not actually a table, but this is how sqlglot represents schema names
        schema_name = sg.table(name, db=database)
        self.raw_sql(sg.exp.Create(kind="SCHEMA", this=schema_name, exists=force))

    def drop_schema(
        self, name: str, database: str | None = None, force: bool = False
    ) -> None:
        schema_name = sg.table(name, db=database)
        self.raw_sql(sg.exp.Drop(kind="SCHEMA", this=schema_name, exists=force))

    def to_pyarrow_batches(
        self,
        expr: ir.Expr,
        *,
        params: Mapping[ir.Scalar, Any] | None = None,
        limit: int | str | None = None,
        chunk_size: int = 1_000_000,
        **kwargs: Any,
    ) -> pa.ipc.RecordBatchReader:
        pa = self._import_pyarrow()

        self._register_in_memory_tables(expr)

        frame = self.con.sql(self.compile(expr.as_table(), params, **kwargs))
        return pa.ipc.RecordBatchReader.from_batches(frame.schema(), frame.collect())

    def _to_sqlglot(
        self, expr: ir.Expr, limit: str | None = None, params=None, **_: Any
    ):
        """Compile an Ibis expression to a sqlglot object."""
        table_expr = expr.as_table()

        if limit == "default":
            limit = ibis.options.sql.default_limit
        if limit is not None:
            table_expr = table_expr.limit(limit)

        if params is None:
            params = {}

        sql = translate(table_expr.op(), params=params)
        assert not isinstance(sql, sg.exp.Subquery)

        if isinstance(sql, sg.exp.Table):
            sql = sg.select(STAR).from_(sql)

        assert not isinstance(sql, sg.exp.Subquery)
        return sql

    def compile(
        self, expr: ir.Expr, limit: str | None = None, params=None, **kwargs: Any
    ):
        """Compile an Ibis expression to a DataFusion SQL string."""
        return self._to_sqlglot(expr, limit=limit, params=params, **kwargs).sql(
            dialect=self.dialect, pretty=True
        )

    def execute(
        self,
        expr: ir.Expr,
        params: Mapping[ir.Expr, object] | None = None,
        limit: int | str | None = "default",
        **kwargs: Any,
    ):
        output = self.to_pyarrow(expr.as_table(), params=params, limit=limit, **kwargs)
        return expr.__pandas_result__(output.to_pandas(timestamp_as_object=True))
