from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, DateTime, Text, JSON, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from sqlalchemy import event

SQLALCHEMY_DATABASE_URL = "sqlite:///./videos.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_size=20,
    max_overflow=40,
    pool_timeout=60
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Video(Base):
    __tablename__ = "videos"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    url = Column(String)
    source_url = Column(String) # For JIT link refreshing
    thumbnail_path = Column(String)
    gif_preview_path = Column(String)
    preview_path = Column(String)
    duration = Column(Float, default=0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    batch_name = Column(String, index=True)
    tags = Column(String, default="") 
    ai_tags = Column(String, default="")
    subtitle = Column(Text, default="")
    sprite_path = Column(String, nullable=True)
    is_favorite = Column(Boolean, default=False)
    is_watched = Column(Boolean, default=False)
    resume_time = Column(Float, default=0)
    status = Column(String, default="pending")
    error_msg = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class SmartPlaylist(Base):
    __tablename__ = "smart_playlists"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, unique=True)
    rules = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    from sqlalchemy import inspect
    inspector = inspect(engine)
    if not inspector.has_table("videos"):
        Base.metadata.create_all(bind=engine)
    else:
        columns = [c['name'] for c in inspector.get_columns('videos')]
        if 'sprite_path' not in columns:
            with engine.connect() as connection:
                connection.execute(text('ALTER TABLE videos ADD COLUMN sprite_path VARCHAR'))
        if 'source_url' not in columns:
            with engine.connect() as connection:
                connection.execute(text('ALTER TABLE videos ADD COLUMN source_url VARCHAR'))

    if not inspector.has_table("smart_playlists"):
         Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
