"""Library schema. Changes go through Alembic migrations only (see db/migrations)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class LibraryRootRow(Base):
    """Mirror of a ``[[libraries]]`` entry from the config (the source of truth),
    plus runtime state. Rows are never deleted automatically, so items survive a
    root disappearing from config by mistake."""

    __tablename__ = "library_root"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    configured: Mapped[bool] = mapped_column(Boolean, default=True)
    kids: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    unavailable_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list[MediaItem]] = relationship(back_populates="root")


class MediaItem(Base):
    __tablename__ = "media_item"
    __table_args__ = (UniqueConstraint("root_id", "rel_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("library_root.id", ondelete="CASCADE"))
    rel_path: Mapped[str] = mapped_column(Text)  # POSIX, relative to the root
    size: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)

    # video | audio | unknown (unknown = probe failed)
    media_type: Mapped[str] = mapped_column(String(8), default="unknown")
    # film | episode | track | home_video | other
    kind: Mapped[str] = mapped_column(String(16), default="other", index=True)

    title: Mapped[str] = mapped_column(Text, default="")
    sort_title: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer)
    show: Mapped[str | None] = mapped_column(Text, index=True)
    season: Mapped[int | None] = mapped_column(Integer)
    episode: Mapped[int | None] = mapped_column(Integer)
    episode_end: Mapped[int | None] = mapped_column(Integer)
    artist: Mapped[str | None] = mapped_column(Text)
    album_artist: Mapped[str | None] = mapped_column(Text)
    album: Mapped[str | None] = mapped_column(Text, index=True)
    track_no: Mapped[int | None] = mapped_column(Integer)
    disc_no: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # What the path/tags said, rewritten by every scan. The display fields above
    # follow these only while the item is unmatched (metadata_source == "file").
    parsed_title: Mapped[str] = mapped_column(Text, default="")
    parsed_year: Mapped[int | None] = mapped_column(Integer)
    parsed_show: Mapped[str | None] = mapped_column(Text, index=True)

    # Metadata (Phase 2): from .nfo, TMDb, or the user.
    metadata_source: Mapped[str] = mapped_column(String(8), default="file")  # file|nfo|tmdb|manual
    match_status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_candidates: Mapped[str | None] = mapped_column(Text)  # JSON list when ambiguous
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of user-set fields
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    imdb_id: Mapped[str | None] = mapped_column(String(16))
    overview: Mapped[str | None] = mapped_column(Text)
    genres: Mapped[str | None] = mapped_column(Text)  # ", "-joined
    certification: Mapped[str | None] = mapped_column(String(16))
    rating: Mapped[float | None] = mapped_column(Float)
    people: Mapped[str | None] = mapped_column(Text)  # JSON {"director": [...], "cast": [...]}
    poster_url: Mapped[str | None] = mapped_column(Text)  # remote, downloaded on demand
    backdrop_url: Mapped[str | None] = mapped_column(Text)

    duration: Mapped[float | None] = mapped_column(Float)
    container: Mapped[str | None] = mapped_column(String(64))
    bitrate: Mapped[int | None] = mapped_column(BigInteger)
    vcodec: Mapped[str | None] = mapped_column(String(32))
    vprofile: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    pix_fmt: Mapped[str | None] = mapped_column(String(32))
    # smpte2084 (PQ: HDR10/Dolby Vision) or arib-std-b67 (HLG) mean HDR.
    color_transfer: Mapped[str | None] = mapped_column(String(24))
    fps: Mapped[float | None] = mapped_column(Float)
    acodec: Mapped[str | None] = mapped_column(String(32))
    channels: Mapped[int | None] = mapped_column(Integer)
    has_embedded_art: Mapped[bool] = mapped_column(Boolean, default=False)

    # Audio health check (library/audiocheck.py): ok | warn | error | dismissed.
    audio_check: Mapped[str | None] = mapped_column(String(12), index=True)
    audio_note: Mapped[str | None] = mapped_column(Text)
    audio_level_db: Mapped[float | None] = mapped_column(Float)
    audio_dual_mono: Mapped[bool | None] = mapped_column(Boolean)
    audio_checked_mtime: Mapped[int | None] = mapped_column(BigInteger)
    audio_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    probe_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    probe_error: Mapped[str | None] = mapped_column(Text)
    probe_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    root: Mapped[LibraryRootRow] = relationship(back_populates="items")
    streams: Mapped[list[MediaStream]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="MediaStream.index"
    )


class ShowMeta(Base):
    """Series-level metadata, keyed by the show name parsed from folders."""

    __tablename__ = "show_meta"

    parsed_show: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    metadata_source: Mapped[str] = mapped_column(String(8), default="file")
    match_status: Mapped[str] = mapped_column(String(12), default="pending")
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_candidates: Mapped[str | None] = mapped_column(Text)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked: Mapped[str] = mapped_column(Text, default="[]")
    tmdb_id: Mapped[int | None] = mapped_column(Integer)
    imdb_id: Mapped[str | None] = mapped_column(String(16))
    overview: Mapped[str | None] = mapped_column(Text)
    genres: Mapped[str | None] = mapped_column(Text)
    certification: Mapped[str | None] = mapped_column(String(16))
    rating: Mapped[float | None] = mapped_column(Float)
    people: Mapped[str | None] = mapped_column(Text)
    poster_url: Mapped[str | None] = mapped_column(Text)
    backdrop_url: Mapped[str | None] = mapped_column(Text)


HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})


def is_hdr(item: MediaItem) -> bool:
    return (item.color_transfer or "") in HDR_TRANSFERS


class MediaStream(Base):
    __tablename__ = "media_stream"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))  # video | audio | subtitle | attachment | data
    codec: Mapped[str | None] = mapped_column(String(32))
    profile: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(16))
    title: Mapped[str | None] = mapped_column(Text)
    channels: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    pix_fmt: Mapped[str | None] = mapped_column(String(32))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_forced: Mapped[bool] = mapped_column(Boolean, default=False)
    attached_pic: Mapped[bool] = mapped_column(Boolean, default=False)

    item: Mapped[MediaItem] = relationship(back_populates="streams")


class Profile(Base):
    """Who's watching. Child profiles see kids libraries only; leaving one
    needs the exit code. Profile 1 is the house default: it can be renamed
    but not removed or made a child (see picturehouse.profiles)."""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    child: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeviceProfile(Base):
    """The profile a device (by network address) is using. Kept on the
    server, not in a cookie, so clearing a browser doesn't leave the child
    profile; devices not listed use profile 1."""

    __tablename__ = "device_profile"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PlaybackState(Base):
    """Resume position per profile and item."""

    __tablename__ = "playback_state"

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profile.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("media_item.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[float] = mapped_column(Float, default=0.0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Device(Base):
    """A client seen on the network (DLNA renderers browsing us, browsers)."""

    __tablename__ = "device"
    __table_args__ = (UniqueConstraint("address", "user_agent"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(Text, default="")
    via: Mapped[str] = mapped_column(String(16))  # dlna | web
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
