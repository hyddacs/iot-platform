"""
数据库连接管理
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session

from app.config import get_settings
from app.models import Base

settings = get_settings()

# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=20,
    max_overflow=10
)

# 创建session工厂
SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autocommit=False,
    autoflush=False
)


def get_db():
    """获取数据库会话的依赖项"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """为后台线程和启动流程提供上下文管理器形式的会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """初始化数据库表（如果不存在）"""
    Base.metadata.create_all(bind=engine)
    ensure_compatible_columns()
    ensure_compatible_indexes()


def ensure_compatible_columns():
    """为已有开发库补充新增字段；正式环境建议迁移到 Alembic。"""
    inspector = inspect(engine)

    column_specs = {
        "devices": {
            "is_maintenance": "BOOLEAN DEFAULT FALSE",
            "factory": "VARCHAR(100) NULL",
            "workshop": "VARCHAR(100) NULL",
            "production_line": "VARCHAR(100) NULL",
            "device_group": "VARCHAR(100) NULL",
            "deleted_at": "DATETIME NULL",
        },
        "alerts": {
            "status": "VARCHAR(20) DEFAULT 'active'",
            "first_triggered_at": "DATETIME NULL",
            "last_triggered_at": "DATETIME NULL",
            "occurrence_count": "INT DEFAULT 1",
            "recovered_at": "DATETIME NULL",
        },
    }

    with engine.begin() as connection:
        for table_name, specs in column_specs.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, ddl in specs.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))

        connection.execute(text(
            "UPDATE alerts SET status = CASE WHEN is_resolved = TRUE THEN 'resolved' ELSE COALESCE(status, 'active') END"
        ))
        connection.execute(text(
            "UPDATE alerts SET first_triggered_at = COALESCE(first_triggered_at, created_at), "
            "last_triggered_at = COALESCE(last_triggered_at, updated_at, created_at), "
            "occurrence_count = COALESCE(occurrence_count, 1)"
        ))


def ensure_compatible_indexes():
    """补充性能优化依赖的索引；正式环境建议迁移到 Alembic。"""
    inspector = inspect(engine)
    index_specs = {
        "alerts": {
            "idx_alerts_active_lookup": (
                "device_id",
                "sensor_field_en",
                "alert_type",
                "status",
                "is_resolved",
            ),
            "idx_alerts_type_resolved_time": (
                "alert_type",
                "is_resolved",
                "last_triggered_at",
            ),
        },
    }

    with engine.begin() as connection:
        for table_name, specs in index_specs.items():
            existing_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
            for index_name, columns in specs.items():
                if index_name in existing_indexes:
                    continue
                column_sql = ", ".join(columns)
                connection.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({column_sql})"))
