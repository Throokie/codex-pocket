package io.github.codexpocket.app;
import java.security.*;import java.security.spec.*;import java.util.*;
public final class NetworkSecurityTest {
 static int count;static void check(String name,boolean value){if(!value)throw new AssertionError(name);count++;System.out.println("PASS "+name);}
 interface Operation{void run()throws Exception;}static void reject(String name,Operation r){try{r.run();throw new AssertionError(name);}catch(AssertionError e){throw e;}catch(Exception expected){check(name,true);}}
 public static void main(String[] args)throws Exception{
  check("canonical scheme host port",OriginRules.normalize("HTTPS://EXAMPLE.COM:443/",true).equals("https://example.com"));
  check("IPv6 compressed",OriginRules.normalize("https://[2001:0db8:0:0:0:0:0:1]:443",true).equals("https://[2001:db8::1]"));
  check("Tailscale debug allowed",OriginRules.normalize("http://100.64.1.2:32848",true).equals("http://100.64.1.2:32848"));
  reject("release HTTP denied",()->OriginRules.normalize("http://10.0.2.2:32848",false));reject("public HTTP denied",()->OriginRules.normalize("http://100.128.1.2",true));reject("fake private DNS denied",()->OriginRules.normalize("http://10.evil.example",true));reject("credential URL denied",()->OriginRules.normalize("https://owner:secret@example.com",true));reject("IPv6 zone denied",()->OriginRules.normalize("https://[fe80::1%25eth0]",true));
  KeyPairGenerator g=KeyPairGenerator.getInstance("EC");g.initialize(new ECGenParameterSpec("secp256r1"));KeyPair pair=g.generateKeyPair();String key=Base64.getEncoder().encodeToString(pair.getPublic().getEncoded()),id=IdentityProof.hex(MessageDigest.getInstance("SHA-256").digest(pair.getPublic().getEncoded())),nonce=IdentityProof.nonce(),origin="https://trusted.example";
  IdentityProof.Pin pin=IdentityProof.pin(id,key,"p256-sha256");Signature signer=Signature.getInstance("SHA256withECDSA");signer.initSign(pair.getPrivate());signer.update(IdentityProof.message(nonce,id,origin));String signature=Base64.getEncoder().encodeToString(signer.sign());
  IdentityProof.verify(pin,nonce,origin,1,id,key,nonce,origin,signature);check("fresh origin-bound signature accepted",true);
  reject("nonce replay denied",()->IdentityProof.verify(pin,IdentityProof.nonce(),origin,1,id,key,nonce,origin,signature));
  reject("candidate relay origin denied",()->IdentityProof.verify(pin,nonce,"https://evil.example",1,id,key,nonce,origin,signature));
  reject("changed origin signature denied",()->IdentityProof.verify(pin,nonce,"https://evil.example",1,id,key,nonce,"https://evil.example",signature));
  reject("claimed ID without key rejected",()->IdentityProof.pin("0".repeat(64),key,"p256-sha256"));
  KeyPair changed=g.generateKeyPair();String changedKey=Base64.getEncoder().encodeToString(changed.getPublic().getEncoded());reject("substituted public key denied",()->IdentityProof.verify(pin,nonce,origin,1,id,changedKey,nonce,origin,signature));
  reject("unsupported algorithm denied",()->IdentityProof.pin(id,key,"trust-all"));
  g.initialize(new ECGenParameterSpec("secp384r1"));KeyPair wrong=g.generateKeyPair();String wrongKey=Base64.getEncoder().encodeToString(wrong.getPublic().getEncoded()),wrongId=IdentityProof.hex(MessageDigest.getInstance("SHA-256").digest(wrong.getPublic().getEncoded()));reject("non-P256 key denied",()->IdentityProof.pin(wrongId,wrongKey,"p256-sha256"));
  var http=new RoutePolicy.View("http://10.0.0.1",true,1,0);var secureDown=new RoutePolicy.View("https://down.example",false,100,0);var secure=new RoutePolicy.View("https://secure.example",true,100,0);var fast=new RoutePolicy.View("https://fast.example",true,20,0);
  check("auto never downgrades to HTTP",RoutePolicy.choose(List.of(http,secureDown),"auto","",http.url(),100000,0).isEmpty());
  check("manual down does not fallback",RoutePolicy.choose(List.of(secure,secureDown),"manual",secureDown.url(),secure.url(),100000,0).isEmpty());
  check("hysteresis holds healthy route",RoutePolicy.choose(List.of(secure,fast),"auto","",secure.url(),10000,0).equals(secure.url()));
  RoutePolicy.Gate gate=new RoutePolicy.Gate();check("HTTP exclusion bypasses healthy gate",gate.apply(List.of(http,secureDown),"auto","",http.url(),true).isEmpty());check("HTTPS adoption bypasses HTTP two-round gate",gate.apply(List.of(http,secure),"auto",secure.url(),http.url(),true).equals(secure.url()));
  gate=new RoutePolicy.Gate();check("first fast round holds",gate.apply(List.of(secure,fast),"auto",fast.url(),secure.url(),true).equals(secure.url()));check("messages cannot count as second probe",gate.apply(List.of(secure,fast),"auto",fast.url(),secure.url(),false).equals(secure.url()));check("second fast round upgrades",gate.apply(List.of(secure,fast),"auto",fast.url(),secure.url(),true).equals(fast.url()));
  check("stale old grant cannot clear new routes",RoutingGrantPolicy.decide(true,false,true,1,2)==RoutingGrantPolicy.Action.REJECT);check("stale null read cannot clear new grant",RoutingGrantPolicy.decide(false,false,true,-1,2)==RoutingGrantPolicy.Action.REJECT);check("authoritative revoke clears routes",RoutingGrantPolicy.decide(false,false,false,-1,2)==RoutingGrantPolicy.Action.CLEAR);check("new current grant replaces old routes",RoutingGrantPolicy.decide(true,true,true,3,2)==RoutingGrantPolicy.Action.REPLACE);
  check("EWMA weighted",Math.abs(RoutePolicy.ewma(100,200)-130)<0.001);System.out.println("PASS TOTAL "+count);
 }
}
