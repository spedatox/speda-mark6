# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Place-search results, kept server-side so the map card can be rich without the
model retyping a directory.

Same reasoning as models/route.py, one step further. `find_places` gets back a
dozen fields per result — name, address, rating, review count, open-now, price,
phone, website, opening hours, the canonical Google Maps link, the place id. All
of it is needed for a card the owner can actually tap ("who is this? is it open?
call them / take me there"), and none of it is worth spending model tokens on
twice: once to read, once to copy into a JSON fence. Copied by hand it also
drifts — a rating rounds, a phone number loses a digit, an address gains a
district it never had.

So the whole result set is parked here under one short id and the fence carries
`"places": "pl_1a2b3c4d"`. The model still reasons over the summary it was given
and still recommends; the client fetches the truth and draws it.

Rows are pruned after RETENTION_DAYS — long enough for a message to be reopened
from history and re-render exactly as it first did.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

RETENTION_DAYS = 45


class PlaceSet(Base):
    __tablename__ = "place_sets"
    __table_args__ = (Index("ix_place_sets_created", "created_at"),)

    # "pl_1a2b3c4d" — short enough that a model either reproduces it exactly or
    # produces something that is plainly not an id.
    id: Mapped[str] = mapped_column(String(24), primary_key=True)

    # What was searched for and from where, so the card can title itself
    # ("BARBERS · 8 NEARBY") without the model restating it.
    query: Mapped[str] = mapped_column(String(200), default="")
    center_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    center_lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    # JSON list of place records — see services/places.py for the shape.
    places_json: Mapped[str] = mapped_column(Text, default="[]")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
