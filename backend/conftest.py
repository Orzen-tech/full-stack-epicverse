# Keep pytest away from the legacy scripts: they connect to live services
# (DATABASE_URL, the deployed backend, OpenAI, GCP) and are not unit tests.
# The real, isolated test suite lives in tests/ (see pytest.ini).
collect_ignore = [
    "test_backend.py",
    "test_db_v2.py",
    "test_postgres_live.py",
    "test_quick.py",
    "test_single.py",
    "scripts",
]
