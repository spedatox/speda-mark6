from sqlalchemy import inspect,text
from scripts.migrate_native_rest import migrate


async def test_additive_upgrade_retains_rows_and_second_apply_is_empty(memory):
    async with memory.engine.begin() as db:
        await db.exec_driver_sql('ALTER TABLE compat_automation_admissions DROP COLUMN created_at')
        await db.exec_driver_sql('ALTER TABLE native_automations DROP COLUMN connection_binding')
        await db.exec_driver_sql('ALTER TABLE native_telegram_inputs DROP COLUMN model')
    plan=await migrate(memory.engine)
    assert not plan['applied'] and len(plan['add_columns'])==3
    result=await migrate(memory.engine,apply=True)
    assert result['applied'] and not result['data_replacement']
    again=await migrate(memory.engine)
    assert not again['new_tables'] and not again['add_columns']
    async with memory.engine.connect() as db:
        assert await db.scalar(text('SELECT count(*) FROM users'))==2
