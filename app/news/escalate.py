"""Preserve news flashes through the renovated automation admission boundary."""
import hashlib
import logging
from app.schemas.trigger import TriggerRequest
from app.services.coordination_budget import CoordinationError

logger = logging.getLogger(__name__)


async def fire_news_flash(app, *, keyword, item):
    service = getattr(getattr(app, 'state', None), 'automation_admissions', None)
    if service is None:
        logger.warning('news_flash_no_admission_service')
        return
    identity = hashlib.sha256(f'news:{item.id}:{keyword}'.encode()).hexdigest()
    body = TriggerRequest(idempotency_key=identity, output_mode='push', payload={
        'job':'news_flash', 'keyword':keyword[:500], 'item':{'title':item.title[:1000],
            'url':item.url[:4096], 'outlet':item.outlet[:100]},
        'task':'Assess this watched headline; corroborate with at most one news_deep_dive if necessary. Return SKIP if no notification is warranted.'})
    try:
        return await service.start(1, 'friday', body)
    except CoordinationError as exc:
        # Intake has already been journalled. n8n can drain it after the hold;
        # no detached model loop, guessed delivery or automatic paid retries.
        logger.warning('news_flash_admission_held', extra={'code':exc.code, 'request_id':identity})
        return {'accepted':False, 'request_id':identity, 'reason':exc.code}
