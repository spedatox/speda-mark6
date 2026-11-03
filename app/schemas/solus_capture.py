"""Facts Sentinel supplies; posting sides, totals, dates and keys are server-owned."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Capture(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: int | None = Field(default=None, gt=0)
    attachment_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    quote: str = Field(min_length=1, max_length=12000)
    quote_start: int | None = Field(default=None, ge=0)
    book: str | None = Field(default=None, max_length=120)
    kind: Literal['expense','income','transfer','card_purchase','card_payment','bill','invoice','bill_payment','invoice_payment','loan_disbursement','refund']
    amount: str = Field(min_length=1, max_length=32)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    date_phrase: str | None = Field(default=None, max_length=64)
    due_date_phrase: str | None = Field(default=None, max_length=64)
    document_number: str | None = Field(default=None, min_length=1, max_length=200)
    from_account: str | None = Field(default=None, max_length=120)
    to_account: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    installments: int = Field(default=1, ge=1, le=600, strict=True)

    @model_validator(mode='after')
    def one_source(self):
        if self.source_id and self.attachment_id: raise ValueError('Use one original message or attachment source.')
        return self
