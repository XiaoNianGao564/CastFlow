from castflow.config import settings

if settings.use_real_db:
    from castflow.db.mysql import db  # noqa: F401
else:
    from castflow.db.mock import db  # noqa: F401
