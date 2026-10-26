"""Reusable configuration invitations, without device grants or implicit approval."""
import base64
import io
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from .network import canonical_origin
from .store import Fault

CONFIG_PREFIX = 'codex-pocket-config-v1'
PAIR_PREFIX = 'codex-pocket-config-pair-v1'
URI_PREFIX = 'codexpocket://import#v1.'
MAX_URI = 2400
MAX_PAYLOAD = 16384


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _pinned_host(host):
    if host.endswith('.ts.net'):
        return True
    try:
        value = ipaddress.ip_address(host)
        return any(value in network for network in (ipaddress.ip_network(n) for n in (
            '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', 'fc00::/7')) if value.version == network.version)
    except ValueError:
        return False


class ConfigLinks:
    def __init__(self, hub):
        self.hub = hub

    def metadata(self):
        value = self.hub.store.config_link()
        active = isinstance(value.get('id'), str) and re.fullmatch('[0-9a-f]{64}', value['id']) is not None
        return {'enabled': active, 'active': active, **{key: value[key] for key in ('generation', 'created_at', 'updated_at') if key in value}}

    def _bootstrap(self, config_id, seeds=False):
        discovery = self.hub.discovery
        if discovery is None:
            raise Fault('config_unavailable', '可复用配置链接需要启用自动HTTPS发现；旧版仍可使用一次性二维码', 503)
        options = discovery.options()
        if (options.get('version') != 1 or options.get('enabled') is not True
                or not isinstance(options.get('tls_spki'), str) or not re.fullmatch('[0-9a-f]{64}', options['tls_spki'])
                or options.get('service_type') != '_codexpocket._tcp.'
                or type(options.get('revision')) is not int or options['revision'] < 0):
            raise Fault('config_unavailable', '自动HTTPS身份尚未准备好', 503)
        allowed = {entry.get('url') for entry in self.hub.store.setting('network_endpoints', []) if isinstance(entry, dict)}
        endpoints = []
        seen = set()
        for raw in discovery.current:
            if not isinstance(raw, dict):
                continue
            try:
                url = canonical_origin(raw.get('url'))
            except Fault:
                continue  # Config invitations never carry legacy cleartext routes.
            if url not in allowed or url in seen:
                continue
            tls, source, kind = raw.get('tls', 'system'), raw.get('source', 'manual'), raw.get('kind', 'private')
            if (tls not in ('system', 'pinned') or source not in ('manual', 'auto')
                    or kind not in ('lan', 'tailscale', 'private', 'public')
                    or source == 'auto' and tls != 'pinned'
                    or tls == 'pinned' and not _pinned_host(urlsplit(url).hostname or '')):
                continue
            label = raw.get('label', '电脑地址')
            if not isinstance(label, str):
                label = '电脑地址'
            endpoints.append({'url': url, 'label': ''.join(c for c in label if ord(c) >= 32 and ord(c) != 127)[:40],
                              'kind': kind, 'tls': tls, 'managed': True, 'source': source})
            seen.add(url)
            if len(endpoints) == 6:
                break
        pinned = [entry for entry in endpoints if entry['tls'] == 'pinned']
        if not pinned:
            raise Fault('config_unavailable', '尚无可供手机安全导入的私网或Tailscale地址', 503)

        def priority(entry):
            host = urlsplit(entry['url']).hostname or ''
            try:
                address = ipaddress.ip_address(host)
                if address.version == 4 and address in ipaddress.ip_network('100.64.0.0/10'):
                    return 0
                if address.version == 4 and entry['kind'] == 'lan':
                    return 1
            except ValueError:
                pass
            return 2 if host.endswith('.ts.net') else 3
        preferred = min(pinned, key=priority)
        if seeds:
            # Stable DNS remains a bootstrap fallback if a numeric private IP changes.
            stable = next((entry for entry in pinned if (urlsplit(entry['url']).hostname or '').endswith('.ts.net')), None)
            lan = next((entry for entry in pinned if entry['kind'] == 'lan'), None)
            ordered = []
            for entry in [preferred, stable, lan, *pinned]:
                if entry is not None and entry['url'] not in {item['url'] for item in ordered}:
                    ordered.append(entry)
            endpoints = ordered[:3]
        name = self.hub.name if isinstance(self.hub.name, str) else '我的电脑'
        name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127)[:80] or '我的电脑'
        return {'type': 'codex-pocket-config', 'version': 1, 'name': name, 'config_id': config_id,
                'server_identity': self.hub.identity.public(), 'discovery': options,
                'revision': options['revision'], 'url': preferred['url'], 'endpoints': endpoints}

    @staticmethod
    def _challenge(challenge):
        if not isinstance(challenge, str) or not re.fullmatch('[0-9a-f]{64}', challenge):
            raise Fault('bad_challenge', '配置请求参数无效')

    def _proof(self, challenge, payload, prefix):
        text = _serialize(payload)
        if len(text.encode()) > MAX_PAYLOAD:
            raise Fault('config_too_large', '配置内容过长，请在电脑减少线路信息', 503)
        data = f'{prefix}\n{challenge}\n{self.hub.identity.id}\n{text}'.encode()
        signature = base64.b64encode(self.hub.identity.key.sign(data, ec.ECDSA(hashes.SHA256()))).decode()
        return {'protocol': 1, 'server_id': self.hub.identity.id, 'challenge': challenge, 'payload': text, 'signature': signature}

    def admin_link(self, rotate=False):
        current = self.hub.store.config_link()
        candidate = current.get('id') if not rotate else None
        if not isinstance(candidate, str) or not re.fullmatch('[0-9a-f]{64}', candidate):
            candidate = secrets.token_hex(32)
        public = self._bootstrap(candidate, seeds=True)
        while True:
            encoded = base64.urlsafe_b64encode(_serialize(public).encode()).decode().rstrip('=')
            link = URI_PREFIX + encoded
            if len(link.encode()) <= MAX_URI:
                break
            if len(public['endpoints']) <= 1:
                raise Fault('config_too_large', '配置链接过长，请缩短电脑名称或线路信息')
            public['endpoints'].pop()
        import qrcode
        output = io.BytesIO()
        qrcode.make(link, error_correction=qrcode.constants.ERROR_CORRECT_L).save(output, format='PNG')
        # Validate and build the QR before changing the active invitation.
        self.hub.store.install_config_link(candidate)
        return {'link': link, 'qr_data_url': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode(),
                'bootstrap': public, **public}

    def get(self, config_id, challenge):
        self.hub.store.require_config_link(config_id)
        self._challenge(challenge)
        return self._proof(challenge, self._bootstrap(config_id), CONFIG_PREFIX)

    def pair(self, config_id, challenge):
        self.hub.store.require_config_link(config_id)
        self._challenge(challenge)
        public = self._bootstrap(config_id)
        # Check all public fields before minting a one-time invitation.
        payload = {**public, 'type': 'codex-pocket-pair', 'ticket': 'x' * 43, 'expires_at': int(self.hub.store.clock() + 120)}
        if len(_serialize(payload).encode()) > MAX_PAYLOAD:
            raise Fault('config_too_large', '配置内容过长，请在电脑减少线路信息', 503)
        pair = self.hub.store.mint_pair(config_id=config_id)
        payload.update(ticket=pair['ticket'], expires_at=int(pair['expires_at']))
        return self._proof(challenge, payload, PAIR_PREFIX)
