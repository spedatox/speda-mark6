from datetime import date, datetime, timezone

from sqlalchemy import ForeignKey, Index, JSON, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Observation(Base):
    """
    One discrete, provenance-carrying fact — and, under v3, the ONLY place a
    durable fact is stored.

    docs/MEMORY_ARCHITECTURE_V3.md §2: the markdown files under /memories are
    derived output, rendered or composed from these rows. They are not a second
    copy that can disagree; there is no second copy. A fact enters here or it
    does not enter at all.

    Three field groups carry what the files used to carry implicitly:

      what it is ABOUT   `subject` + `domain` — replaces the routing tree. An
                         agent answers "what is this a fact about?", which it can
                         read off the sentence, instead of "which of eight files
                         does this belong in?", which it guesses at under task
                         pressure.
      WHEN it was true   `valid_from` / `valid_until` — replaces demotion. A
                         state that ends is not moved to another file; it gets an
                         end date. current.md and history.md are one query with
                         the filter flipped, so a demotion can no longer half-fail
                         and lose the fact.
      what REPLACED it   `superseded_by` — replaces overwriting. A changed figure
                         links to its replacement instead of being destroyed,
                         which is what makes "what did he earn last year?"
                         answerable and a wrong correction reversible.

    Levels form an evidence ladder (see app/services/observations.py for the
    validation rules that make it structural rather than aspirational):

      explicit      — a direct fact stated in conversation. Needs no sources.
      deductive     — a logical necessity. REQUIRES source_ids + premises.
      inductive     — a pattern across several facts. REQUIRES 2+ source_ids,
                      2+ sources, a pattern_type and a confidence.
      contradiction — two recorded facts that cannot both hold. REQUIRES 2+
                      source_ids + sources.

    Nothing is hard-deleted. `deleted_at` demotes a row out of recall while
    keeping it readable for audit — the same doctrine the memory files follow
    (docs/MEMORY_ARCHITECTURE.md §3.4: "nothing is deleted, only demoted").
    """

    __tablename__ = "observations"
    __table_args__ = (
        # Recall's hot path: this owner's live observations, newest first.
        Index("ix_observations_user_live", "user_id", "deleted_at", "created_at"),
        # "What has Sentinel learned lately?" and the per-agent audit view.
        Index("ix_observations_user_observer", "user_id", "observer"),
        # The renderers' hot path: every surface is a query over (subject, domain)
        # narrowed by whether the fact is still true.
        Index("ix_observations_render", "user_id", "subject", "domain", "valid_until"),
        # history.md and the "what was true then" queries.
        Index("ix_observations_validity", "user_id", "valid_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # Which agent observed this. Mark VI is single-owner, so Honcho's
    # (observer, observed) pair collapses to just the observer — but the observer
    # half is load-bearing: eight agents watch one owner and dossier.md already
    # attributes entries by agent_id. Convergent observations from independent
    # agents are the strongest signal in the store.
    observer: Mapped[str] = mapped_column(String(64))

    # How this row came to exist. Re-indexing has to know what it may replace:
    #   "live"    — recorded by an agent mid-conversation, with the full context
    #               of that turn. NOT re-derivable: a judgement made while
    #               talking to the owner is not reliably reconstructable from the
    #               transcript alone, so a rebuild preserves these.
    #   "reindex" — derived from raw history by the re-indexer. Disposable by
    #               definition; a rebuild deletes and regenerates them, which is
    #               what makes improving the extraction prompt free.
    #   "seed"    — parsed out of the pre-v3 markdown files on the one-time
    #               migration. Preserved, because its source no longer exists.
    #   "owner"   — written by the owner. Preserved, always, and wins conflicts.
    origin: Mapped[str] = mapped_column(String(16), default="live")

    content: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(16), default="explicit")

    # ── What the fact is ABOUT (v3 §3.1) ─────────────────────────────────────
    # `subject` is "owner", "person:<Name>" or "project:<Name>". `domain` is one
    # of app/services/observations.py::DOMAINS. Together they decide which
    # surface the fact renders into — a computed answer, which is why the old
    # `path` column is gone: storing the destination alongside the facts that
    # determine it just creates a second answer that can disagree with the first.
    subject: Mapped[str] = mapped_column(String(128), default="owner")
    domain: Mapped[str] = mapped_column(String(32), default="state")

    # ── WHEN the fact was true (v3 §3.1) ─────────────────────────────────────
    # `valid_from` NULL means "as far back as the record goes" — a biographical
    # constant, not an unknown. `valid_until` NULL means it is still true, and it
    # is the ONLY definition of the present tense in the system: current.md
    # renders the NULLs, history.md renders the non-NULLs. A state that ends is
    # never relocated, so no relocation can lose it.
    valid_from: Mapped[date | None] = mapped_column(nullable=True)
    valid_until: Mapped[date | None] = mapped_column(nullable=True)

    # The observation that replaced this one — a changed figure, a corrected
    # fact. Set together with `valid_until` on the row being replaced. Following
    # the chain backwards is what makes "what did it used to be?" answerable and
    # a mistaken correction reversible.
    superseded_by: Mapped[int | None] = mapped_column(nullable=True)

    # Provenance. source_ids are Observation.id values; premises/sources carry the
    # human-readable text of those sources so a recall result is self-explaining
    # without a second lookup.
    source_ids: Mapped[list] = mapped_column(JSON, default=list)
    premises: Mapped[list] = mapped_column(JSON, default=list)
    sources: Mapped[list] = mapped_column(JSON, default=list)

    # Inductive-only qualifiers.
    pattern_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Where it came from in the raw record — lets recall pull the surrounding
    # turns for context instead of showing a bare assertion.
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id"), nullable=True
    )
    message_ids: Mapped[list] = mapped_column(JSON, default=list)

    # How many times this fact has been independently re-observed. Honcho ranks
    # "most derived" observations by this — a fact seen five times across months
    # outranks one seen once.
    reinforcement_count: Mapped[int] = mapped_column(default=1)

    # L2-normalized float32 vector, same convention as MessageEmbedding, so
    # cosine similarity is a plain dot product (app/services/embeddings.py).
    # Nullable: an observation is recorded even when the embedding call fails,
    # and embed_pending_observations() heals it later.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    # Soft delete — demotion, not destruction. NULL = live.
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)
