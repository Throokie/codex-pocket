package io.github.codexpocket.app;
import java.security.*;
import java.security.cert.*;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
public final class DiscoverySecurityTest {
 interface Work{void run()throws Exception;}
 static int tests;static void check(boolean value,String label){if(!value)throw new AssertionError(label);tests++;System.out.println("PASS "+label);}static void reject(Work work,String label)throws Exception{try{work.run();}catch(Exception expected){check(true,label);return;}throw new AssertionError(label);}
 static String sign(PrivateKey key,String nonce,String id,String payload)throws Exception{Signature s=Signature.getInstance("SHA256withECDSA");s.initSign(key);s.update(DiscoveryProof.message(nonce,id,payload));return Base64.getEncoder().encodeToString(s.sign());}
 public static void main(String[] args)throws Exception{
  KeyPairGenerator gen=KeyPairGenerator.getInstance("EC");gen.initialize(new java.security.spec.ECGenParameterSpec("secp256r1"));KeyPair keys=gen.generateKeyPair();String id=IdentityProof.hex(MessageDigest.getInstance("SHA-256").digest(keys.getPublic().getEncoded()));IdentityProof.Pin pin=IdentityProof.pin(id,Base64.getEncoder().encodeToString(keys.getPublic().getEncoded()),"p256-sha256");String nonce=IdentityProof.nonce(),payload="{\"revision\":2,\"endpoints\":[{\"url\":\"https://10.0.0.2:32851\"}]}",signature=sign(keys.getPrivate(),nonce,id,payload);
  DiscoveryProof.verify(pin,nonce,1,id,nonce,payload,signature);check(true,"signed exact manifest");
  reject(()->DiscoveryProof.verify(pin,nonce,1,id,IdentityProof.nonce(),payload,signature),"replayed nonce");
  reject(()->DiscoveryProof.verify(pin,nonce,1,"0".repeat(64),nonce,payload,signature),"wrong server identity");
  reject(()->DiscoveryProof.verify(pin,nonce,1,id,nonce,payload+" ",signature),"payload bytes modified");
  reject(()->DiscoveryProof.verify(pin,nonce,1,id,nonce,"x".repeat(16385),signature),"payload bound");
  DiscoveryProof.revision(2,"same",2,"same");check(true,"same revision same content");DiscoveryProof.revision(2,"same",3,"next");check(true,"new revision accepted");
  reject(()->DiscoveryProof.revision(2,"same",1,"old"),"old revision rejected");reject(()->DiscoveryProof.revision(2,"same",2,"other"),"same revision changed content rejected");
  X509Certificate cert=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(Files.newInputStream(Path.of(args[0],"pinned-test.pem")));String tlsPin=IdentityProof.hex(MessageDigest.getInstance("SHA-256").digest(cert.getPublicKey().getEncoded()));PinnedTls.check(cert,tlsPin);check(true,"QR-pinned self signed leaf accepted");reject(()->PinnedTls.check(cert,"0".repeat(64)),"forged NSD certificate rejected");
  X509Certificate expired=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(Files.newInputStream(Path.of(args[0],"expired-test.pem")));reject(()->PinnedTls.check(expired,tlsPin),"expired pinned leaf rejected");
  check(!DiscoveryProof.current(false,2,2),"old pairing manifest rejected");check(!DiscoveryProof.current(true,2,3),"old network generation manifest rejected");check(DiscoveryProof.current(true,3,3),"current manifest generation accepted");
  javax.net.ssl.HttpsURLConnection system=(javax.net.ssl.HttpsURLConnection)new java.net.URL("https://example.invalid").openConnection();javax.net.ssl.SSLSocketFactory factory=system.getSSLSocketFactory();javax.net.ssl.HostnameVerifier verifier=system.getHostnameVerifier();PinnedTls.apply(system,null);check(system.getSSLSocketFactory()==factory&&system.getHostnameVerifier()==verifier,"system TLS defaults untouched");PinnedTls.apply(system,tlsPin);check(javax.net.ssl.HttpsURLConnection.getDefaultSSLSocketFactory()==factory&&javax.net.ssl.HttpsURLConnection.getDefaultHostnameVerifier()==verifier,"pin override never global");
  javax.net.ssl.SSLSocketFactory same=PinnedTls.factory("https://same.invalid",tlsPin);check(same==PinnedTls.factory("https://SAME.invalid:443/",tlsPin),"same canonical origin and pin reuse factory");
  check(same!=PinnedTls.factory("https://other.invalid",tlsPin),"different origin factory isolated");check(same!=PinnedTls.factory("https://same.invalid","1".repeat(64)),"different pin factory isolated");
  javax.net.ssl.HttpsURLConnection second=(javax.net.ssl.HttpsURLConnection)new java.net.URL("https://example.invalid/other").openConnection();PinnedTls.apply(second,tlsPin);check(system.getSSLSocketFactory()==second.getSSLSocketFactory()&&system.getHostnameVerifier()==second.getHostnameVerifier(),"same route retains factory and verifier for pooling");
  javax.net.ssl.SSLSocketFactory evicted=PinnedTls.factory("https://evicted.invalid",tlsPin);for(int i=0;i<16;i++)PinnedTls.factory("https://cache-"+i+".invalid",tlsPin);check(PinnedTls.cachedPolicies()==16,"TLS policy cache bounded to sixteen");check(evicted!=PinnedTls.factory("https://evicted.invalid",tlsPin),"least recently used policy evicted");
  reject(()->PinnedTls.factory("http://10.0.0.1",tlsPin),"cache rejects plaintext origin");
  String selected=VerifiedCandidates.choose(List.of("unreachable-primary","reachable-secondary"),origin->{if(origin.startsWith("unreachable"))throw new java.io.IOException();},500);check(selected.equals("reachable-secondary"),"QR primary unavailable alternate proof selected");
  AtomicInteger live=new AtomicInteger(),max=new AtomicInteger();String chosen=VerifiedCandidates.choose(List.of("a","b","c","d","e","f"),origin->{int n=live.incrementAndGet();max.accumulateAndGet(n,Math::max);try{Thread.sleep(30);}finally{live.decrementAndGet();}},500);check(max.get()<=3&&!chosen.isEmpty(),"pair proofs bounded concurrency");
  reject(()->VerifiedCandidates.choose(List.of("a","b"),origin->{throw new java.io.IOException();},100),"no verified origin fails closed");
  System.out.println("PASS TOTAL "+tests);
 }
}
