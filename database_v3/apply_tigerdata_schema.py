"""Run the additive TigerData schema once after reviewing the SQL file.

Usage: .\\.venv\\Scripts\\python.exe apply_tigerdata_schema.py
"""
import os
from pathlib import Path
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT.parent / '.env')
url = os.getenv('TIMESCALE_SERVICE_URL')
if not url:
    raise RuntimeError('TIMESCALE_SERVICE_URL is missing from .env')
script = (ROOT / '01_tigerdata_schema.sql').read_text(encoding='utf-8')
with psycopg.connect(url, connect_timeout=10) as conn:
    conn.execute(script)
print('Chronos v3 TigerData tables are ready. No existing rows were deleted.')
