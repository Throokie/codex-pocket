"""Private SQLite state: device grants, one-time pairing and durable delivery receipts."""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .private_files import require_posix, ensure_private_dir, create_private, read_private, check_private


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Fault(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Store:
    def __init__(self, directory, clock=time.time):
        require_posix()
        self.directory = ensure_private_dir(directory)
        self.clock = clock
        keyfile = self.directory / 'pair-envelope.key'
        try:
            create_private(keyfile, secrets.token_bytes(32))
        except FileExistsError:
            pass
        self.cipher = AESGCM(read_private(keyfile))
        database = self.directory / 'pocket.sqlite3'
        try:
            create_private(database, b'')
        except FileExistsError:
            pass
        check_private(database)
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                check_private(sidecar)
        self.db = sqlite3.connect(database, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS devices(
              id TEXT PRIMARY KEY,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,
              created_at REAL NOT NULL,last_seen REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pairings(
              id TEXT PRIMARY KEY,ticket_hash TEXT UNIQUE NOT NULL,expires_at REAL NOT NULL,
              status TEXT NOT NULL DEFAULT 'issued',poll_hash TEXT,nonce_hash TEXT,
              device_name TEXT,phrase TEXT,device_id TEXT,token_envelope TEXT);
            CREATE TABLE IF NOT EXISTS deliveries(
              device_id TEXT NOT NULL,client_id TEXT NOT NULL,body_hash TEXT NOT NULL,
              kind TEXT NOT NULL,target TEXT NOT NULL,status TEXT NOT NULL,
              created_at REAL NOT NULL,updated_at REAL NOT NULL,
              PRIMARY KEY(device_id,client_id));
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS media(
              id TEXT PRIMARY KEY,device_id TEXT NOT NULL,mime TEXT NOT NULL,
              size INTEGER NOT NULL,path TEXT NOT NULL,created_at REAL NOT NULL,used INTEGER DEFAULT 0);
        ''')
        # SQLite derives sidecar permissions from the pre-created database (0600).
        for suffix in ('', '-wal', '-shm'):
            check_private(Path(str(database) + suffix))
        if 'config_id' not in {row['name'] for row in self.db.execute('PRAGMA table_info(pairings)')}:
            self.db.execute('ALTER TABLE pairings ADD COLUMN config_id TEXT')
        self.db.execute("UPDATE deliveries SET status='unknown' WHERE status='sending'")

    def close(self):
        self.db.close()

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))

    def config_link(self):
        value = self.setting('config_link', {})
        return value if isinstance(value, dict) else {}

    def config_link_matches(self, candidate):
        import re
        current = self.config_link().get('id')
        valid = isinstance(candidate, str) and re.fullmatch('[0-9a-f]{64}', candidate) is not None
        configured = isinstance(current, str) and re.fullmatch('[0-9a-f]{64}', current) is not None
        actual = current if configured else '0' * 64
        return hmac.compare_digest(candidate if valid else '0' * 64, actual) and valid and configured

    def require_config_link(self, candidate):
        if not self.config_link_matches(candidate):
            raise Fault('config_unavailable', '配置链接不存在或已更换，请向电脑获取新链接', 404)

    def install_config_link(self, candidate):
        import re
        if not isinstance(candidate, str) or not re.fullmatch('[0-9a-f]{64}', candidate):
            raise Fault('bad_config_id', '配置链接标识无效')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.config_link()
            if old.get('id') == candidate:
                self.db.execute('COMMIT')
                return old
            now = self.clock()
            value = {'id': candidate, 'generation': int(old.get('generation', 0)) + 1,
                     'created_at': old.get('created_at', now), 'updated_at': now}
            self.set_setting('config_link', value)
            if isinstance(old.get('id'), str):
                self.db.execute("UPDATE pairings SET status='denied',token_envelope=NULL WHERE config_id=? AND status IN ('issued','pending')", (old['id'],))
            self.db.execute('COMMIT')
            return value
        except Exception:
            self.db.execute('ROLLBACK')
            raise

    def mint_pair(self, ttl=120, config_id=None):
        self.cleanup_pairings()
        if config_id is not None:
            self.require_config_link(config_id)
            active_link = self.db.execute("SELECT count(*) FROM pairings WHERE config_id=? AND status IN ('issued','pending') AND expires_at>?", (config_id, self.clock())).fetchone()[0]
            if active_link >= 3:
                raise Fault('pair_limit', '此配置链接待配对请求较多，请先完成或拒绝已有请求', 429)
        active = self.db.execute("SELECT count(*) FROM pairings WHERE status IN ('issued','pending') AND expires_at>?", (self.clock(),)).fetchone()[0]
        if active >= 10:
            raise Fault('pair_limit', '待配对请求过多，请先完成或撤销', 429)
        rid, ticket = str(uuid.uuid4()), secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO pairings(id,ticket_hash,expires_at,config_id) VALUES(?,?,?,?)', (rid, digest(ticket), self.clock() + ttl, config_id))
        return {'request_id': rid, 'ticket': ticket, 'expires_at': self.clock() + ttl}

    def claim(self, ticket, name, nonce):
        if not isinstance(ticket, str) or len(ticket) > 160 or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not isinstance(nonce, str) or not 16 <= len(nonce) <= 160:
            raise Fault('bad_pair', '配对参数无效')
        poll = secrets.token_urlsafe(32)
        words = ['松林', '月光', '纸船', '山谷', '星河', '微风', '青石', '雪峰', '远帆', '湖畔', '晴空', '麦田', '雨声', '竹影', '海岸', '晨雾']
        phrase = ' · '.join(secrets.choice(words) for _ in range(5))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM pairings WHERE ticket_hash=?', (digest(ticket),)).fetchone()
            if not row or row['expires_at'] <= self.clock():
                raise Fault('pair_expired', '配对码不存在或已过期', 410)
            if row['config_id'] is not None:
                self.require_config_link(row['config_id'])
            if row['status'] != 'issued':
                raise Fault('pair_consumed', '配对码已使用，请重新生成', 409)
            self.db.execute("UPDATE pairings SET status='pending',poll_hash=?,nonce_hash=?,device_name=?,phrase=? WHERE id=?", (digest(poll), digest(nonce), name.strip(), phrase, row['id']))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return {'request_id': row['id'], 'poll_token': poll, 'phrase': phrase, 'expires_at': row['expires_at']}

    def pair_status(self, rid, poll):
        row = self.db.execute('SELECT * FROM pairings WHERE id=?', (rid,)).fetchone()
        if not row or not isinstance(poll, str) or not hmac.compare_digest(row['poll_hash'] or '', digest(poll)):
            raise Fault('pair_forbidden', '配对请求无效', 403)
        if row['expires_at'] <= self.clock():
            return {'status': 'expired'}
        result = {'status': row['status'], 'phrase': row['phrase'], 'expires_at': row['expires_at']}
        if row['status'] == 'approved':
            device = self.db.execute('SELECT revoked FROM devices WHERE id=?', (row['device_id'],)).fetchone()
            if not device or device['revoked']:
                return {'status': 'denied'}
            data = base64.urlsafe_b64decode(row['token_envelope'])
            token = self.cipher.decrypt(data[:12], data[12:], rid.encode()).decode()
            result.update(token=token, device_id=row['device_id'])
        return result

    def decide_pair(self, rid, approve, phrase):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT * FROM pairings WHERE id=?', (rid,)).fetchone()
            if not row or row['expires_at'] <= self.clock():
                raise Fault('pair_expired', '配对请求已过期', 410)
            if row['config_id'] is not None:
                self.require_config_link(row['config_id'])
            if row['status'] != 'pending':
                raise Fault('pair_not_pending', '请求已处理', 409)
            if not isinstance(phrase, str) or not hmac.compare_digest(phrase.encode(), row['phrase'].encode()):
                raise Fault('phrase_mismatch', '请先核对手机显示的完整短语', 409)
            if approve:
                did, token = str(uuid.uuid4()), secrets.token_urlsafe(40)
                self.db.execute('INSERT INTO devices VALUES(?,?,?,?,?,0)', (did, row['device_name'], digest(token), self.clock(), self.clock()))
                iv = secrets.token_bytes(12)
                envelope = base64.urlsafe_b64encode(iv + self.cipher.encrypt(iv, token.encode(), rid.encode())).decode()
                # A short encrypted envelope permits safe status retry after a lost HTTP response.
                self.db.execute("UPDATE pairings SET status='approved',device_id=?,token_envelope=?,expires_at=? WHERE id=?", (did, envelope, self.clock() + 120, rid))
            else:
                self.db.execute("UPDATE pairings SET status='denied' WHERE id=?", (rid,))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return {'ok': True}

    def authenticate(self, token):
        if not token or len(token) > 160:
            raise Fault('device_revoked', '设备未配对或已被撤销，请重新扫码', 401)
        row = self.db.execute('SELECT id,name,created_at,last_seen FROM devices WHERE token_hash=? AND revoked=0', (digest(token),)).fetchone()
        if not row:
            raise Fault('device_revoked', '设备未配对或已被撤销，请重新扫码', 401)
        if self.clock() - row['last_seen'] > 30:
            self.db.execute('UPDATE devices SET last_seen=? WHERE id=?', (self.clock(), row['id']))
        return dict(row)

    def devices(self, current=None):
        return [dict(r, current=r['id'] == current) for r in self.db.execute('SELECT id,name,created_at,last_seen FROM devices WHERE revoked=0 ORDER BY created_at')]

    def revoke(self, did):
        self.db.execute('UPDATE devices SET revoked=1 WHERE id=?', (did,))
        self.db.execute('UPDATE pairings SET token_envelope=NULL,status=\'denied\' WHERE device_id=?', (did,))

    def pending_pairs(self):
        self.cleanup_pairings()
        return [dict(r) for r in self.db.execute("SELECT id,device_name,phrase,expires_at FROM pairings WHERE status='pending' AND expires_at>?", (self.clock(),))]

    def cleanup_pairings(self):
        self.db.execute("UPDATE pairings SET status='expired',token_envelope=NULL WHERE expires_at<=? AND status NOT IN ('expired','denied')", (self.clock(),))
        self.db.execute('DELETE FROM pairings WHERE expires_at<?', (self.clock() - 3600,))

    def reserve(self, device, client_id, kind, target, body):
        try:
            uuid.UUID(client_id)
        except (ValueError, TypeError, AttributeError):
            raise Fault('bad_client_id', '发送编号必须是UUID')
        hashed = digest(json.dumps({'kind': kind, 'target': target, 'body': body}, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        row = self.db.execute('SELECT body_hash,status FROM deliveries WHERE device_id=? AND client_id=?', (device, client_id)).fetchone()
        if row:
            if row['body_hash'] != hashed:
                raise Fault('nonce_conflict', '同一发送编号不能更换目标或内容', 409)
            return {'status': 'unknown' if row['status'] == 'sending' else row['status'], 'client_id': client_id, 'duplicate': True}
        now = self.clock()
        self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)', (device, client_id, hashed, kind, target, 'sending', now, now))
        return None

    def finish(self, device, client_id, status):
        self.db.execute('UPDATE deliveries SET status=?,updated_at=? WHERE device_id=? AND client_id=?', (status, self.clock(), device, client_id))
