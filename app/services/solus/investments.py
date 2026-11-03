# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit native-currency cost lots. No guessed quotes or jurisdictional tax policy."""
import re
from datetime import date
from collections import defaultdict
from sqlalchemy import select
from app.models.solus_ledger import SolusLot, SolusDisposal, SolusValuation, SolusEvent
from app.schemas.solus import Post, InvestmentBuy, InvestmentSell, ValuationPut
from app.services.solus.money import minor, money
from app.services.solus.commands import account_for, book_for, SolusError


def units(value, scale):
    if not isinstance(value,str) or not re.fullmatch(r'\d+(?:\.\d+)?',value):
        raise SolusError('Quantity must be an unsigned exact decimal string.')
    whole,_,fraction=value.partition('.')
    if len(fraction)>scale:
        raise SolusError('Quantity exceeds this instrument precision.')
    result=int(whole)*10**scale+int(fraction.ljust(scale,'0') or '0')
    if not 0<result<=10**15:
        raise SolusError('Quantity must be positive and within the supported precision range.')
    return result


async def remainder(db,lot,as_of):
    original=await db.get(SolusEvent,lot.event_id)
    reversed_ids=select(SolusEvent.reverses_id).where(SolusEvent.posted.is_(True),SolusEvent.reverses_id.is_not(None),SolusEvent.occurred_on<=as_of)
    if original.occurred_on>as_of or await db.scalar(select(SolusEvent.id).where(SolusEvent.reverses_id==original.id,
        SolusEvent.posted.is_(True),SolusEvent.occurred_on<=as_of)):
        return 0,0
    disposals=(await db.execute(select(SolusDisposal).join(SolusEvent,SolusEvent.id==SolusDisposal.event_id).where(
        SolusDisposal.lot_id==lot.id,SolusEvent.posted.is_(True),SolusEvent.occurred_on<=as_of,SolusEvent.id.not_in(reversed_ids)))).scalars().all()
    return lot.quantity-sum(d.quantity for d in disposals),lot.cost_minor-sum(d.cost_minor for d in disposals)


async def assert_reversible(db,event):
    lot=await db.scalar(select(SolusLot).where(SolusLot.event_id==event.id))
    if lot:
        quantity,_=await remainder(db,lot,date.max)
        if quantity!=lot.quantity:
            raise SolusError('Reverse active lot disposals before reversing its acquisition.',409)
    disposal=await db.scalar(select(SolusDisposal).where(SolusDisposal.event_id==event.id))
    if disposal:
        reversed_ids=select(SolusEvent.reverses_id).where(SolusEvent.posted.is_(True),SolusEvent.reverses_id.is_not(None))
        later=await db.scalar(select(SolusDisposal.id).where(SolusDisposal.lot_id==disposal.lot_id,
            SolusDisposal.id>disposal.id,SolusDisposal.event_id.not_in(reversed_ids)).limit(1))
        if later:
            raise SolusError('Reverse later lot disposals first to preserve cumulative cost allocation.',409)


async def position_state(db,book_id,account_id,instrument,as_of):
    lots=(await db.execute(select(SolusLot).where(SolusLot.book_id==book_id,SolusLot.account_id==account_id,
        SolusLot.instrument==instrument))).scalars().all()
    quantity,cost,scale=0,0,None
    for lot in lots:
        q,c=await remainder(db,lot,as_of)
        quantity+=q;cost+=c;scale=lot.unit_scale
    return quantity,cost,scale


async def apply_investment(db,command,context,evidence):
    from app.services.solus.commands import apply
    book=await book_for(db,context.user_id,command.book_id)
    if isinstance(command,ValuationPut):
        account=await account_for(db,book.id,command.account_id)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if command.as_of>datetime.now(ZoneInfo(book.timezone)).date():
            raise SolusError('A valuation needs an actual observation date.')
        quantity,cost,scale=await position_state(db,book.id,account.id,command.instrument,command.as_of)
        if not quantity or quantity>10**15 or cost>10**15:
            raise SolusError('No supported holding exists at this valuation date.')
        row=SolusValuation(book_id=book.id,account_id=account.id,instrument=command.instrument,as_of=command.as_of,
            quantity=quantity,unit_scale=scale,cost_minor=cost,value_minor=minor(command.value,account.currency,allow_zero=True),evidence=evidence)
        db.add(row);book.revision+=1;await db.flush()
        return {'book_id':book.id,'valuation_id':row.id,'revision':book.revision}
    cash=await account_for(db,book.id,command.cash_account_id)
    if cash.account_type!='asset':
        raise SolusError('Select an asset account for the actual cash movement.')
    amount_currency=cash.currency
    fee=minor(command.fee,amount_currency,allow_zero=True)
    lines=[]
    def line(account,side,value):
        if value:
            lines.append({'account_id':account.id,'side':side,'amount':money(value,account.scale)})
    if isinstance(command,InvestmentBuy):
        holding=await account_for(db,book.id,command.holding_account_id)
        if holding.account_type!='asset' or holding.id==cash.id or holding.currency!=amount_currency:
            raise SolusError('Use separate holding and cash assets in the same cost currency; record FX separately.')
        scales=(await db.execute(select(SolusLot.unit_scale).where(SolusLot.account_id==holding.id,
            SolusLot.instrument==command.instrument))).scalars().all()
        if scales and any(s!=command.unit_scale for s in scales):
            raise SolusError('Reuse this instrument precision for all its lots.')
        quantity=units(command.quantity,command.unit_scale)
        cost=minor(command.cost,amount_currency)
        basis=cost+fee if command.fee_treatment=='capitalize' else cost
        line(holding,'debit',basis);line(cash,'credit',cost+fee)
        instrument=command.instrument
    else:
        lot=await db.get(SolusLot,command.lot_id)
        if not lot or lot.book_id!=book.id:
            raise SolusError('Cost lot not found in this book.',404)
        holding=await account_for(db,book.id,lot.account_id)
        if holding.currency!=amount_currency or holding.id==cash.id:
            raise SolusError('Sale proceeds and the selected cost lot need separate accounts in one currency.')
        quantity=units(command.quantity,lot.unit_scale)
        remaining,remaining_cost=await remainder(db,lot,command.occurred_on)
        # Backdating into an already disposed lot would rewrite subsequent cost
        # allocations. Such a correction requires reversing later disposals first.
        total_remaining,_=await remainder(db,lot,date.max)
        if remaining!=total_remaining:
            raise SolusError('Reverse later disposals before backdating a sale.',409)
        if quantity>remaining:
            raise SolusError('Sale quantity exceeds the remaining cost lot.')
        basis=remaining_cost if quantity==remaining else lot.cost_minor*((lot.quantity-remaining)+quantity)//lot.quantity-(lot.cost_minor-remaining_cost)
        proceeds=minor(command.proceeds,amount_currency)
        if proceeds<=fee:
            raise SolusError('Net sale proceeds must be positive; use a documented journal for zero-value disposals.')
        gain=await account_for(db,book.id,command.gain_account_id);loss=await account_for(db,book.id,command.loss_account_id)
        if gain.account_type!='income' or loss.account_type!='expense' or gain.currency!=amount_currency or loss.currency!=amount_currency:
            raise SolusError('Select native-currency gain-income and loss-expense accounts.')
        line(cash,'debit',proceeds-fee);line(holding,'credit',basis)
        if proceeds>=basis:
            line(gain,'credit',proceeds-basis)
        else:
            line(loss,'debit',basis-proceeds)
        instrument=lot.instrument
    if fee and (isinstance(command,InvestmentSell) or command.fee_treatment=='expense'):
        expense=await account_for(db,book.id,command.fee_account_id)
        if expense.account_type!='expense' or expense.currency!=amount_currency:
            raise SolusError('Select a native-currency expense account for the fee.')
        line(expense,'debit',fee)
    post=Post.model_validate({'operation':'post','book_id':book.id,'command_key':command.command_key,
        'occurrence_key':command.occurrence_key,'kind':'journal','description':f"{'Buy' if isinstance(command,InvestmentBuy) else 'Sell'} {command.quantity} {instrument}",
        'occurred_on':command.occurred_on,'lines':lines})
    result=await apply(db,post,context,evidence)
    if isinstance(command,InvestmentBuy):
        row=SolusLot(book_id=book.id,event_id=result['event_id'],account_id=holding.id,instrument=instrument,
            quantity=quantity,unit_scale=command.unit_scale,cost_minor=basis)
        db.add(row);await db.flush();result['lot_id']=row.id
    else:
        db.add(SolusDisposal(book_id=book.id,event_id=result['event_id'],lot_id=lot.id,quantity=quantity,cost_minor=basis))
        await db.flush();result['cost_basis']=money(basis,holding.scale)
    return result


async def positions(db,user_id,book_id,period):
    from app.services.solus.reports import period_bounds
    _,last=period_bounds(period)
    await book_for(db,user_id,book_id)
    rows=(await db.execute(select(SolusLot).where(SolusLot.book_id==book_id).order_by(SolusLot.instrument,SolusLot.id))).scalars().all()
    result=[]
    for lot in rows:
        quantity,cost=await remainder(db,lot,last)
        if not quantity:
            continue
        account=await account_for(db,book_id,lot.account_id)
        result.append({'id':lot.id,'event_id':lot.event_id,'account_id':lot.account_id,'account':account.name,'instrument':lot.instrument,
            'quantity':money(quantity,lot.unit_scale),'unit_scale':lot.unit_scale,'cost':money(cost,account.scale),'currency':account.currency})
    valuations=[]
    keys={(r['account_id'],r['instrument']) for r in result}
    for account_id,instrument in sorted(keys):
        row=await db.scalar(select(SolusValuation).where(SolusValuation.account_id==account_id,SolusValuation.instrument==instrument,
            SolusValuation.as_of<=last).order_by(SolusValuation.as_of.desc(),SolusValuation.created_at.desc(),SolusValuation.id.desc()).limit(1))
        if not row:
            continue
        quantity,cost,_=await position_state(db,book_id,account_id,instrument,row.as_of)
        account=await account_for(db,book_id,account_id)
        valid=quantity==row.quantity and cost==row.cost_minor
        valuations.append({'account_id':account_id,'instrument':instrument,'as_of':row.as_of.isoformat(),
            'value':money(row.value_minor,account.scale),'currency':account.currency,
            'unrealized_at_valuation':money(row.value_minor-row.cost_minor,account.scale) if valid else None,
            'status':'dated valuation; not income' if valid else 'basis changed; valuation needs review'})
    return {'period':period,'lots':result,'valuations':valuations,
        'scope':'Explicit specific-lot disposals and cumulative proportional cost allocation; fees follow your chosen treatment. Native cost currency only. Supplied dated valuations never create income or replace actual balances. No automatic tax calculation.'}
