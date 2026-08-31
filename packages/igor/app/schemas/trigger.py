# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from typing import Literal

from pydantic import BaseModel


class TriggerRequest(BaseModel):
    payload: dict
    output_mode: Literal["push", "silent"]


class TriggerResponse(BaseModel):
    accepted: bool
    request_id: str
