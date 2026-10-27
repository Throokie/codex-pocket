"""Server identity for explicit routes. TLS still protects the HTTP channel."""
import base64
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from .store import Fault
from .private_files import require_posix, ensure_private_dir, create_private, read_private, replace_private


def canonical_origin(value, allow_http=False):
    if not isinstance(value, str) or not value or len(value)>600 or value!=value.strip() or any(ord(c)<=32 or ord(c)==127 for c in value) or '\\' in value:
        raise Fault('bad_url', '请输入完整服务地址，不含路径、空格或登录信息')
    try:
        u=urlsplit(value)
        if u.scheme not in ('https','http') or not u.hostname or u.username is not None or u.password is not None or u.query or u.fragment or u.path not in ('','/') or '%' in u.netloc:
            raise ValueError()
        host=u.hostname.lower();port=u.port
        if port is not None and not 1<=port<=65535:raise ValueError()
        try:
            ip=ipaddress.ip_address(host);host=ip.compressed
        except ValueError:
            ip=None;host=host.encode('idna').decode('ascii')
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?',host) or '..' in host:raise ValueError()
        if u.scheme=='http':
            trusted=host=='localhost' or ip is not None and any(ip in net for net in [ipaddress.ip_network(n) for n in ('127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','::1/128','fc00::/7') ] if ip.version==net.version)
            if not allow_http or not trusted:raise Fault('insecure_url','正式连接请使用HTTPS；HTTP仅允许显式开启的可信私网开发地址')
        authority='['+host+']' if ':' in host else host
        if port is not None and port != (443 if u.scheme=='https' else 80):authority+=':'+str(port)
        return u.scheme+'://'+authority
    except (ValueError,UnicodeError):
        raise Fault('bad_url','服务地址无效，请检查协议、主机和端口')


def endpoints(values, primary=None, allow_http=False):
    if values is None:values=[]
    if not isinstance(values,list) or len(values)>6:raise Fault('bad_endpoints','最多设置6条网络线路')
    result=[];seen=set()
    for value in values:
        if not isinstance(value,dict):raise Fault('bad_endpoints','线路格式无效')
        url=canonical_origin(value.get('url'),allow_http)
        label=value.get('label','');kind=value.get('kind','private')
        if not isinstance(label,str) or len(label)>40 or any(ord(c)<32 for c in label) or kind not in ('lan','tailscale','private','public'):
            raise Fault('bad_endpoints','请检查线路名称和网络类型')
        if url not in seen:
            result.append({'url':url,'label':label.strip() or '服务线路','kind':kind});seen.add(url)
    if primary:
        primary=canonical_origin(primary,allow_http)
        if primary not in seen:result.insert(0,{'url':primary,'label':'主地址','kind':'private'})
    if len(json.dumps(result,ensure_ascii=False).encode())>1700:raise Fault('bad_endpoints','线路内容过长，请缩短名称或地址后重新生成配对码')
    if not 1<=len(result)<=6:raise Fault('bad_endpoints','请设置1至6条不同线路')
    return result


class ServerIdentity:
    def __init__(self,directory):
        require_posix()
        directory=ensure_private_dir(directory)
        path=directory/'server-identity.pem'
        if not path.exists():
            key=ec.generate_private_key(ec.SECP256R1())
            raw=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
            try:
                create_private(path,raw)
            except FileExistsError:pass
        self.key=serialization.load_pem_private_key(read_private(path),password=None)
        if not isinstance(self.key,ec.EllipticCurvePrivateKey) or not isinstance(self.key.curve,ec.SECP256R1):raise RuntimeError('服务身份类型无效')
        raw=self.key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        self.public_key=base64.b64encode(raw).decode();self.id=hashlib.sha256(raw).hexdigest()
    def public(self):
        return {'id':self.id,'key':self.public_key,'algorithm':'p256-sha256'}
    def proof(self,challenge,origin,allowed):
        if not isinstance(challenge,str) or not re.fullmatch('[0-9a-f]{64}',challenge):raise Fault('bad_challenge','身份检测参数无效')
        origin=canonical_origin(origin,allow_http=True)
        if origin not in {e['url'] for e in allowed}:raise Fault('route_unregistered','请先在电脑管理页登记此线路地址',403)
        data=f'codex-pocket-route-v1\n{challenge}\n{self.id}\n{origin}'.encode()
        signature=base64.b64encode(self.key.sign(data,ec.ECDSA(hashes.SHA256()))).decode()
        return {'protocol':1,'server_id':self.id,'public_key':self.public_key,'challenge':challenge,'origin':origin,'signature':signature}


class ServerTLS:
    """Dedicated TLS key, pinned by QR. Never replaces public/system TLS trust."""
    def __init__(self,directory):
        require_posix()
        import datetime
        import ssl
        from cryptography import x509
        from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
        directory=ensure_private_dir(directory)
        self.directory=directory
        keypath=directory/'server-tls-key.pem';certpath=directory/'server-tls-cert.pem'
        if not keypath.exists():
            key=ec.generate_private_key(ec.SECP256R1())
            try:
                create_private(keypath,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            except FileExistsError:pass
        key=serialization.load_pem_private_key(read_private(keypath),None)
        if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):raise RuntimeError('TLS密钥类型无效')
        public=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        self.spki=hashlib.sha256(public).hexdigest();now=datetime.datetime.now(datetime.timezone.utc);renew=True
        if certpath.exists():
            cert=x509.load_pem_x509_certificate(read_private(certpath))
            if cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)!=public:raise RuntimeError('TLS证书与密钥不匹配，需恢复原备份')
            renew=cert.not_valid_after_utc<now+datetime.timedelta(days=30)
        if renew:
            subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Codex Pocket local service')])
            cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-datetime.timedelta(minutes=10)).not_valid_after(now+datetime.timedelta(days=730))
                .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('codex-pocket.local'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
                .sign(key,hashes.SHA256()))
            replace_private(certpath,cert.public_bytes(serialization.Encoding.PEM))
        self.context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version=ssl.TLSVersion.TLSv1_2
        self.context.options|=ssl.OP_NO_COMPRESSION
        self.context.load_cert_chain(certpath,keypath)

    def refresh_certificate(self):
        # Renew with the same private key so paired phones keep the same SPKI pin.
        renewed=ServerTLS(self.directory)
        if renewed.spki!=self.spki:raise RuntimeError('TLS身份变更，需要恢复原密钥或重新配对')
        self.context.load_cert_chain(self.directory/'server-tls-cert.pem',self.directory/'server-tls-key.pem')
