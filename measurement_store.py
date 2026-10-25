"""Private per-account playback measurements; never stores camera frames."""
import hashlib
import json
import math
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class MeasurementStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tickets (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    name TEXT NOT NULL, expires REAL NOT NULL, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, user_id TEXT NOT NULL,
                    name TEXT NOT NULL, started TEXT NOT NULL, ended TEXT,
                    status TEXT NOT NULL, metadata TEXT NOT NULL, last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    run_id TEXT NOT NULL, metric TEXT NOT NULL, timestamp_us TEXT NOT NULL,
                    value REAL NOT NULL, confidence REAL, stable INTEGER NOT NULL,
                    playback_seconds REAL NOT NULL, voice_on INTEGER, background_on INTEGER,
                    UNIQUE(run_id, metric, timestamp_us)
                );
                CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, recorded TEXT NOT NULL, kind TEXT NOT NULL,
                    playback_seconds REAL NOT NULL, detail TEXT NOT NULL
                );
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue_ticket(self, user_id, name, metadata):
        if not user_id or not name:
            raise PermissionError('A verified account is required.')
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('INSERT INTO tickets VALUES (?, ?, ?, ?, ?)',
                       (self.digest(token), user_id, name, time.time() + 7200, json.dumps(metadata)))
        return token

    def ticket(self, db, token):
        row = db.execute('SELECT * FROM tickets WHERE token_hash=? AND expires>?',
                         (self.digest(token), time.time())).fetchone()
        if row is None:
            raise PermissionError('Session authorisation expired. Refresh the player.')
        return row

    def start(self, token, resume=None):
        with self.connect() as db:
            ticket = self.ticket(db, token)
            if resume:
                run = db.execute('SELECT * FROM runs WHERE id=? AND token_hash=?', (resume, ticket['token_hash'])).fetchone()
                if run is None or run['status'] not in ('recording', 'paused', 'interrupted'):
                    raise PermissionError('Run cannot be resumed.')
                db.execute('UPDATE runs SET status=?, ended=NULL, last_seen=? WHERE id=?', ('recording', time.time(), resume))
                return resume
            run_id = uuid4().hex
            db.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)',
                       (run_id, ticket['token_hash'], ticket['user_id'], ticket['name'], utc_now(), 'recording', ticket['metadata'], time.time()))
            return run_id

    def authorise_run(self, db, token, run_id):
        ticket = self.ticket(db, token)
        row = db.execute('SELECT * FROM runs WHERE id=? AND token_hash=?', (run_id, ticket['token_hash'])).fetchone()
        if row is None:
            raise PermissionError('Run does not belong to this session.')
        return row

    def event(self, token, run_id, kind, position=0, detail=None):
        allowed = {'playing', 'paused', 'completed', 'failed', 'interrupted', 'levels', 'validation'}
        if kind not in allowed:
            raise ValueError('Unknown event.')
        with self.connect() as db:
            row = self.authorise_run(db, token, run_id)
            if row['status'] in ('completed', 'failed', 'interrupted'):
                return
            metadata = json.loads(row['metadata'])
            position = float(position)
            if not math.isfinite(position) or not 0 <= position <= metadata['duration_seconds'] + 2:
                raise ValueError('Invalid playback position.')
            status = {'playing': 'recording', 'paused': 'paused'}.get(kind, kind if kind in ('completed','failed','interrupted') else row['status'])
            ended = utc_now() if status in ('completed', 'failed', 'interrupted') else None
            db.execute('UPDATE runs SET status=?, ended=?, last_seen=? WHERE id=?', (status, ended, time.time(), run_id))
            db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)', (run_id, utc_now(), kind, position, json.dumps(detail or {})))

    def ingest(self, token, run_id, metrics, position, levels, timestamp_us=None):
        with self.connect() as db:
            row = self.authorise_run(db, token, run_id)
            if row['status'] != 'recording':
                return
            duration = json.loads(row['metadata'])['duration_seconds']
            position = float(position)
            if not math.isfinite(position) or not 0 <= position <= duration + 2:
                raise ValueError('Invalid playback position.')
            for metric, group, field in [('pulse', 'cardio', 'pulseRate'), ('breathing', 'breathing', 'rate')]:
                for sample in metric_samples(metrics, group, field):
                    if not isinstance(sample, dict):
                        continue
                    value = sample.get('value')
                    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                        continue
                    stamp = str(sample.get('timestamp') if sample.get('timestamp') is not None else timestamp_us or '')
                    if not stamp:
                        continue
                    confidence = sample.get('confidence')
                    if not isinstance(confidence, (float, int)) or not math.isfinite(confidence):
                        confidence = None
                    db.execute('INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                               (run_id, metric, stamp, value, confidence, sample.get('stable') is True, position,
                                bool(levels.get('voice_on')), bool(levels.get('background_on'))))
            expression_names = {1:'angry',2:'contempt',3:'disgust',4:'fear',5:'happy',6:'neutral',7:'sad',8:'surprise'}
            for expression in metric_samples(metrics, 'face', 'expression'):
                if not isinstance(expression, dict):
                    continue
                stamp = str(expression.get('timestamp') if expression.get('timestamp') is not None else timestamp_us or '')
                if not stamp:
                    continue
                for score in expression.get('scores', []) if isinstance(expression.get('scores'), list) else []:
                    if not isinstance(score, dict):
                        continue
                    category = score.get('type')
                    name = expression_names.get(category) if isinstance(category, int) else str(category).lower()
                    if name not in expression_names.values():
                        continue
                    confidence = score.get('confidence')
                    if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not math.isfinite(confidence) or not 0 <= confidence <= 100:
                        continue
                    db.execute('INSERT OR IGNORE INTO samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                               (run_id, 'expression:' + name, stamp, confidence, confidence,
                                expression.get('stable') is True, position,
                                bool(levels.get('voice_on')), bool(levels.get('background_on'))))
            db.execute('UPDATE runs SET last_seen=? WHERE id=?', (time.time(), run_id))

    def heartbeat(self, token, run_id):
        with self.connect() as db:
            row = self.authorise_run(db, token, run_id)
            if row['status'] == 'recording':
                db.execute('UPDATE runs SET last_seen=? WHERE id=?', (time.time(), run_id))

    def revoke(self, token):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', ended=? WHERE token_hash=? AND status IN ('paused','recording')", (utc_now(), self.digest(token)))
            db.execute('DELETE FROM tickets WHERE token_hash=?', (self.digest(token),))

    def runs_for(self, user_id):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', ended=? WHERE status='recording' AND last_seen<?", (utc_now(), time.time() - 45))
            return [dict(row) for row in db.execute('SELECT * FROM runs WHERE user_id=? ORDER BY started DESC', (user_id,))]

    def result(self, user_id, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=? AND user_id=?', (run_id, user_id)).fetchone()
            if row is None:
                raise PermissionError('This result is not available for your account.')
            samples = [dict(s) for s in db.execute('SELECT * FROM samples WHERE run_id=? ORDER BY playback_seconds, timestamp_us', (run_id,))]
            events = [dict(s) for s in db.execute('SELECT * FROM events WHERE run_id=? ORDER BY recorded', (run_id,))]
        return {'run': dict(row), 'samples': samples, 'events': events, 'summary': summarise(samples),
                'partial': row['status'] != 'completed',
                'captured_until_seconds': max([s['playback_seconds'] for s in samples] + [e['playback_seconds'] for e in events] + [0])}


def summarise(samples):
    result = {}
    for metric in ('pulse', 'breathing'):
        all_samples = [s for s in samples if s['metric'] == metric]
        usable = [s for s in all_samples if s['stable'] and s['confidence'] is not None and s['confidence'] >= 50]
        summary = {'samples': len(all_samples), 'usable_samples': len(usable), 'comparison': 'insufficient data'}
        if usable:
            low, high = min(s['playback_seconds'] for s in usable), max(s['playback_seconds'] for s in usable)
            early = [s['value'] for s in usable if s['playback_seconds'] <= low + (high-low) * .2]
            late = [s['value'] for s in usable if s['playback_seconds'] >= high - (high-low) * .2]
            if high-low >= 30 and len(early) >= 3 and len(late) >= 3:
                first, last = sum(early)/len(early), sum(late)/len(late)
                summary.update(comparison='descriptive within-session change', early_mean=first, late_mean=last, change=last-first)
        result[metric] = summary
    return result


def metric_samples(metrics, group, field):
    """Skip malformed optional groups without rolling back other measurements."""
    group_value = metrics.get(group) if isinstance(metrics, dict) else None
    samples = group_value.get(field) if isinstance(group_value, dict) else None
    return samples if isinstance(samples, list) else []
