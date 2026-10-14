# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Legacy prototype compatibility during the owner's manual migration.

Never combine these totals with the new double-entry books. Retirement requires
an explicit owner instruction; the original tables remain intact.
"""
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.solus import SolusTransaction, SolusInstallment
from app.services.solus.money import minor, currency_scale, money, allocate, monthly_dates
from app.services.solus.reports import period_bounds


async def log_transaction(db, user_id, entity, transaction_type, total_amount, currency='TRY', transaction_date=None, installments=1):
    if transaction_type not in {'expense','income','transfer'} or not isinstance(entity,str) or not entity.strip():
        raise ValueError('Supply an entity and supported legacy movement.')
    value = minor(total_amount, currency)
    scale = currency_scale(currency)
    if scale != 2:
        raise ValueError('Legacy storage supports two-decimal currencies; use a Solus book for other precisions.')
    occurred = date.fromisoformat(transaction_date) if isinstance(transaction_date,str) else transaction_date
    if not isinstance(occurred,date):
        raise ValueError('Supply an exact occurrence date.')
    # Validate every input before adding anything to the caller-owned session.
    amounts = allocate(value, installments)
    dates = monthly_dates(occurred, installments)
    tx = SolusTransaction(user_id=user_id,entity=entity.strip(),transaction_type=transaction_type,
        total_amount=Decimal(money(value,scale)),currency=currency,date=occurred,installment_count=installments)
    db.add(tx)
    for due, amount in zip(dates, amounts):
        db.add(SolusInstallment(transaction=tx,month_period=due.strftime('%Y-%m'),amount=Decimal(money(amount,scale))))
    await db.flush()
    return tx


async def get_summary(db,user_id,month_period):
    period_bounds(month_period)
    rows=(await db.execute(select(SolusTransaction.transaction_type,SolusTransaction.currency,SolusInstallment.amount).join(
        SolusInstallment,SolusInstallment.transaction_id==SolusTransaction.id).where(
            SolusTransaction.user_id==user_id,SolusInstallment.month_period==month_period))).all()
    totals={}
    for kind,currency,amount in rows:
        bucket=totals.setdefault(currency,{})
        bucket[kind]=bucket.get(kind,Decimal('0.00'))+amount
    return {'period':month_period,'totals':{c:{k:str(v) for k,v in values.items()} for c,values in totals.items()},
        'scope':'Legacy prototype only. Scheduled installments, not actual payments. Never add to Solus book totals.'}


async def get_all_transactions(db,user_id):
    return list((await db.execute(select(SolusTransaction).where(SolusTransaction.user_id==user_id).options(
        selectinload(SolusTransaction.installments)).order_by(SolusTransaction.date.desc(),SolusTransaction.id.desc()))).scalars().all())


async def delete_transaction(db,user_id,transaction_id):
    tx=await db.scalar(select(SolusTransaction).where(SolusTransaction.user_id==user_id,SolusTransaction.id==transaction_id))
    if not tx:
        return False
    await db.delete(tx)
    await db.flush()
    return True
