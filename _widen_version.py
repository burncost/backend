import psycopg2
from app.config import settings

u = (
    settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql")
    .replace("ssl=require", "sslmode=require")
)

conn = psycopg2.connect(u)
try:
    cur = conn.cursor()
    cur.execute(
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"
    )
    conn.commit()
    print("WIDENED OK")
    cur.close()
finally:
    conn.close()
