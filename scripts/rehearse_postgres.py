"""Copy and verify active tables from a frozen SQLite snapshot in isolated PostgreSQL."""

import argparse
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import Boolean, Date, DateTime, Float, JSON, Numeric
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect

from app.database import Base
import app.models  # noqa: F401
from app.models.memory_serving import MemoryText
from scripts.rehearse_sqlite import integrity, quote, readonly


def column_type(column):
    return getattr(column.type, "impl", column.type)


def convert(value, column):
    if value is None:
        return None
    kind = column_type(column)
    if isinstance(kind, Boolean):
        if value not in (0, 1):
            raise ValueError("Invalid boolean")
        return bool(value)
    if isinstance(kind, DateTime):
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo:
            stamp = stamp.astimezone(timezone.utc)
        if kind.timezone:
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        return stamp.replace(tzinfo=None)
    if isinstance(kind, Date):
        return date.fromisoformat(value)
    if isinstance(kind, Numeric) and kind.asdecimal:
        return Decimal(str(value))
    if isinstance(kind, Float):
        return float(value)
    return value


def json_value(value):
    if isinstance(value, bytes):
        return ["bytes", len(value), hashlib.sha256(value).hexdigest()]
    if isinstance(value, (datetime, date, Decimal)):
        return [type(value).__name__, str(value.normalize() if isinstance(value, Decimal) else value)]
    raise TypeError(type(value).__name__)


def hash_row(row):
    encoded = json.dumps(list(row), default=json_value, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).digest()


def fingerprint(hashes):
    digest = hashlib.sha256()
    for item in sorted(hashes):
        digest.update(item)
    return {"rows": len(hashes), "sha256": digest.hexdigest()}


async def copy_table(source, target, table, sqlite_sequences):
    columns = list(table.columns)
    names = [column.name for column in columns]
    quoted = ",".join(map(quote, names))
    target_fields = ",".join(f"{quote(c.name)}::text" if isinstance(column_type(c), JSON)
                             else quote(c.name) for c in columns)
    expected = []
    dialect = postgres_dialect()
    codecs = [col.type if isinstance(col.type, MemoryText) else None for col in columns]
    sqlite_rows = source.execute(f"SELECT {quoted} FROM {quote(table.name)}")
    while batch := sqlite_rows.fetchmany(512):
        records = [tuple(convert(value, column) for value, column in zip(row, columns)) for row in batch]
        expected.extend(map(hash_row, records))
        encoded = [tuple(codec.process_bind_param(value, dialect) if codec else value
                         for value, codec in zip(row, codecs)) for row in records]
        await target.copy_records_to_table(table.name, records=encoded, columns=names, schema_name="public")

    actual = [hash_row(codec.process_result_value(value, dialect) if codec else value
                       for value, codec in zip(row, codecs))
              async for row in target.cursor(f"SELECT {target_fields} FROM public.{quote(table.name)}")]
    baseline = fingerprint(expected)
    if baseline != fingerprint(actual):
        raise ValueError("Row fingerprint mismatch")

    for column in table.primary_key:
        sequence = await target.fetchval("SELECT pg_get_serial_sequence($1, $2)",
                                         f"public.{quote(table.name)}", column.name)
        if sequence:
            largest = await target.fetchval(f"SELECT max({quote(column.name)}) FROM public.{quote(table.name)}")
            high_water = max(largest or 0, sqlite_sequences.get(table.name, 0))
            if high_water > 0:
                await target.fetchval("SELECT setval($1::regclass, $2, true)", sequence, high_water)
    return baseline


async def load(snapshot, url):
    parsed = make_url(url)
    if (parsed.drivername != "postgresql+asyncpg" or parsed.host not in {"127.0.0.1", "localhost", "::1"}
            or parsed.database != "speda_rehearsal" or parsed.query):
        raise ValueError("Use a loopback PostgreSQL database named speda_rehearsal")
    engine = create_async_engine(parsed, hide_parameters=True)
    try:
        with readonly(snapshot) as source:
            integrity(source)
            source.execute("BEGIN")
            source_tables = {name for _, name, kind, *_ in source.execute("PRAGMA table_list")
                             if kind == "table" and not name.startswith("sqlite_")}
            has_sequences = source.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
            sqlite_sequences = dict(source.execute("SELECT name,seq FROM sqlite_sequence")) if has_sequences else {}
            archive = {name: source.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0]
                       for name in sorted(source_tables - Base.metadata.tables.keys())}
            extensions = {}
            results = {}
            async with engine.begin() as connection:
                raw = (await connection.get_raw_connection()).driver_connection
                public_tables = await raw.fetchval("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                if public_tables:
                    raise ValueError("The rehearsal target must be empty")
                await connection.run_sync(Base.metadata.create_all)
                for table in Base.metadata.sorted_tables:
                    try:
                        found = {r[1] for r in source.execute(f"PRAGMA table_info({quote(table.name)})")}
                        expected = {column.name for column in table.columns}
                        if expected - found:
                            raise ValueError("Snapshot lacks required columns")
                        if found - expected:
                            extensions[table.name] = sorted(found - expected)
                        results[table.name] = await copy_table(source, raw, table, sqlite_sequences)
                    except Exception as exc:
                        # Driver details can contain private values and attachment bytes.
                        raise ValueError(f"Rehearsal failed for {table.name} ({type(exc).__name__})") from None
        return {"active_tables": results, "active_rows": sum(row["rows"] for row in results.values()),
                "archive_only_tables": archive, "archive_only_columns": extensions,
                "archive_location": "original SQLite snapshot", "cutover_ready": False}
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Report path already exists")
    result = asyncio.run(load(args.snapshot, os.environ["SPEDA_REHEARSAL_POSTGRES_URL"]))
    with args.report.open("x", encoding="utf-8") as report:
        json.dump(result, report, indent=2)
    print(f'Verified {result["active_rows"]} rows across {len(result["active_tables"])} PostgreSQL tables.')
