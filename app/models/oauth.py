"""Short-lived, one-use authorization requests; never owner memory evidence."""
from sqlalchemy import String, Float
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class OAuthAttempt(Base):
    __tablename__ = 'oauth_attempts'
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    owner_id: Mapped[int]
    expires_at: Mapped[float] = mapped_column(Float)
    config_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    browser_hash: Mapped[str] = mapped_column(String(64), default='')


class OAuthIdentity(Base):
    """Explicit account-link generation; ordinary refresh-token rotation preserves it."""
    __tablename__='oauth_identity_generations'
    user_id:Mapped[int]=mapped_column(primary_key=True)
    provider:Mapped[str]=mapped_column(String(20),primary_key=True)
    generation:Mapped[str]=mapped_column(String(32))
