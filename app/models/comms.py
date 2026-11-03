"""Disposable numeric cursors over original room events for legacy clients."""
from sqlalchemy import ForeignKey,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from app.database import Base


class CommsCursor(Base):
    __tablename__='native_comms_cursors'
    __table_args__=(UniqueConstraint('room_id','sequence'),)
    id:Mapped[int]=mapped_column(primary_key=True,autoincrement=True)
    room_id:Mapped[str]=mapped_column(ForeignKey('coord_rooms.id'))
    sequence:Mapped[int]=mapped_column()
