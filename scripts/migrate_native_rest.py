"""Plan additive native schema changes; apply only with retained backup evidence.

This does not start the application, connect vendors, replace memory or run a
cutover. Stop application writers before applying a reviewed migration.
"""
import argparse
import asyncio
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine
from app.database import Base
import app.models  # noqa: F401


ADDITIONS={
    'compat_automation_admissions':{'created_at':'TIMESTAMP'},
    'native_automations':{'connection_binding':"JSON NOT NULL DEFAULT '{{}}'"},
    'native_telegram_inputs':{'model':'VARCHAR(255)','reply_context':'JSON'},
}


async def migrate(engine,*,apply=False):
    async with engine.begin() as connection:
        if apply and engine.dialect.name=='sqlite':
            raw=await connection.get_raw_connection()
            if not raw.driver_connection.in_transaction:
                await connection.exec_driver_sql('BEGIN IMMEDIATE')
        existing=await connection.run_sync(lambda db:inspect(db).get_table_names())
        changes=[]
        for table,columns in ADDITIONS.items():
            if table not in existing: continue
            names=await connection.run_sync(lambda db: {c['name'] for c in inspect(db).get_columns(table)})
            for name,definition in columns.items():
                if name not in names: changes.append((table,name,definition))
        new_tables=sorted(set(Base.metadata.tables)-set(existing))
        unsupported=[]
        for table in sorted(set(Base.metadata.tables)&set(existing)):
            columns=await connection.run_sync(lambda db: inspect(db).get_columns(table))
            names={column['name'] for column in columns}
            planned={name for name in ADDITIONS.get(table,{})}
            unsupported.extend(table+'.'+column.name for column in Base.metadata.tables[table].columns
                if column.name not in names and column.name not in planned)
            unsupported.extend(table+'.'+column['name']+' (legacy required column)' for column in columns
                if column['name'] not in Base.metadata.tables[table].columns
                and not column['nullable'] and column.get('default') is None and not column.get('primary_key'))
        if apply and unsupported:
            raise ValueError('Unreviewed schema differences: '+', '.join(unsupported))
        if apply:
            for table,name,definition in changes:
                sql=definition.format(timestamp=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
                await connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {sql}')
            await connection.run_sync(Base.metadata.create_all)
            if engine.dialect.name=='sqlite':
                from app.services.solus.guards import install_guards
                await connection.run_sync(install_guards)
        return {'applied':apply,'new_tables':new_tables,'add_columns':[table+'.'+name for table,name,_ in changes],
            'unsupported_columns':unsupported,
            'data_replacement':False,'scheduler_or_vendor_calls':False}


async def main(args):
    url=args.database_url or os.getenv('SPEDA_MIGRATION_DATABASE_URL')
    if not url: raise SystemExit('Provide --database-url or SPEDA_MIGRATION_DATABASE_URL explicitly.')
    if args.apply and (not args.backup or not Path(args.backup).is_file() or Path(args.backup).stat().st_size==0):
        raise SystemExit('Apply requires --backup pointing to the verified retained database backup. Stop writers first.')
    engine=create_async_engine(url)
    try: print(json.dumps(await migrate(engine,apply=args.apply)))
    finally: await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup')
    asyncio.run(main(parser.parse_args()))
