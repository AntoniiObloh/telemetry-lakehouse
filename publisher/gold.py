"""Read srv_thingsboard through databricks-sql-connector (connection from DATABRICKS_*)."""

from typing import Protocol, Self

# Import from databricks.sql, not databricks: the repo's databricks/ folder (the Asset
# Bundle) makes ruff take the bare name for a first-party module.
from databricks.sql import connect
from databricks.sql import exc as sql_exc

from publisher import PublisherError
from publisher.config import Settings
from publisher.payloads import Row


class Gold(Protocol):
    def rows_since(self, since_ms: int) -> list[Row]: ...

    def rows_between(self, start_ms: int, end_ms: int) -> list[Row]: ...

    def max_ts_ms(self) -> int | None: ...


def describe(exc: Exception) -> str:
    """Error type and its first line: enough to act on, without the connector's noise."""
    lines = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {lines[0][:200]}" if lines else type(exc).__name__


class DatabricksGold:
    """One SQL warehouse connection for the whole run; use it as a context manager."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.connection = None

    def __enter__(self) -> Self:
        try:
            self.connection = connect(
                server_hostname=self.settings.databricks_host,
                http_path=self.settings.databricks_http_path,
                access_token=self.settings.databricks_token,
            )
        except sql_exc.Error as exc:
            raise PublisherError(
                f"cannot connect to Databricks ({describe(exc)})"
            ) from None
        return self

    def __exit__(self, *exc_info) -> None:
        if self.connection is not None:
            self.connection.close()

    def query(self, statement: str, parameters: dict | None = None) -> list:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(statement, parameters)
                return cursor.fetchall()
        except sql_exc.Error as exc:
            raise PublisherError(f"Databricks query failed ({describe(exc)})") from None

    def rows(self, where: str, parameters: dict) -> list[Row]:
        rows = self.query(
            "SELECT entity_type, entity_name, ts_ms, key, value"
            f" FROM {self.settings.gold_table} WHERE {where}",
            parameters,
        )
        return [Row(r[0], r[1], int(r[2]), r[3], float(r[4])) for r in rows]

    def rows_since(self, since_ms: int) -> list[Row]:
        return self.rows("ts_ms > :since_ms", {"since_ms": since_ms})

    def rows_between(self, start_ms: int, end_ms: int) -> list[Row]:
        """Rows with start_ms <= ts_ms < end_ms (backfill)."""
        return self.rows(
            "ts_ms >= :start_ms AND ts_ms < :end_ms",
            {"start_ms": start_ms, "end_ms": end_ms},
        )

    def max_ts_ms(self) -> int | None:
        ((newest,),) = self.query(f"SELECT max(ts_ms) FROM {self.settings.gold_table}")
        return None if newest is None else int(newest)
