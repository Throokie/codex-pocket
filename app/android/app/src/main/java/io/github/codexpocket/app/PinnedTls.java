package io.github.codexpocket.app;
import javax.net.ssl.*;
import java.security.*;
import java.security.cert.*;
import java.util.LinkedHashMap;
/** An explicit QR-pinned route only. Default CA/hostname policy remains untouched elsewhere. */
final class PinnedTls {
 private static final int CAPACITY=16;
 private record Key(String origin,String pin){}
 private record Policy(SSLSocketFactory factory,HostnameVerifier verifier){}
 private static final LinkedHashMap<Key,Policy> policies=new LinkedHashMap<>(16,.75f,true);
 private static synchronized Policy policy(String origin,String pin)throws GeneralSecurityException{
  if(pin==null||!pin.matches("[0-9a-f]{64}"))throw new GeneralSecurityException("TLS pin无效");
  final String canonical;try{canonical=OriginRules.normalize(origin,false);}catch(Exception e){throw new GeneralSecurityException("TLS origin无效",e);}
  Key key=new Key(canonical,pin);Policy existing=policies.get(key);if(existing!=null)return existing;
  X509TrustManager trust=new X509TrustManager(){public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}public void checkClientTrusted(X509Certificate[] chain,String type)throws CertificateException{throw new CertificateException("client authentication unsupported");}public void checkServerTrusted(X509Certificate[] chain,String type)throws CertificateException{if(chain==null||chain.length==0)throw new CertificateException("缺少证书");check(chain[0],pin);}};
  SSLContext tls=SSLContext.getInstance("TLS");tls.init(null,new TrustManager[]{trust},null);
  // Stable verifier identity also permits pooled connections; verification still runs for each new TLS connection.
  HostnameVerifier verifier=(hostname,session)->{try{java.security.cert.Certificate[] chain=session.getPeerCertificates();if(chain.length==0||!(chain[0] instanceof X509Certificate leaf))return false;check(leaf,pin);return true;}catch(Exception e){return false;}};
  Policy created=new Policy(tls.getSocketFactory(),verifier);policies.put(key,created);while(policies.size()>CAPACITY)policies.remove(policies.keySet().iterator().next());return created;
 }
 static SSLSocketFactory factory(String origin,String pin)throws GeneralSecurityException{return policy(origin,pin).factory();}
 static synchronized int cachedPolicies(){return policies.size();}

 static void check(X509Certificate leaf,String pin)throws CertificateException{
  if(pin==null||!pin.matches("[0-9a-f]{64}"))throw new CertificateException("TLS pin无效");leaf.checkValidity();
  try{String actual=IdentityProof.hex(MessageDigest.getInstance("SHA-256").digest(leaf.getPublicKey().getEncoded()));if(!MessageDigest.isEqual(actual.getBytes(java.nio.charset.StandardCharsets.US_ASCII),pin.getBytes(java.nio.charset.StandardCharsets.US_ASCII)))throw new CertificateException("TLS证书与配对码不符");}catch(NoSuchAlgorithmException e){throw new CertificateException(e);}
 }
 static void apply(HttpsURLConnection connection,String pin)throws GeneralSecurityException{
  if(pin==null)return;
  java.net.URL url=connection.getURL();Policy policy=policy(url.getProtocol()+"://"+url.getAuthority(),pin);
  connection.setSSLSocketFactory(policy.factory());connection.setHostnameVerifier(policy.verifier());
 }
}
