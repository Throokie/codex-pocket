package io.github.codexpocket.app;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.interfaces.ECPublicKey;
import java.security.spec.*;
import java.util.*;
final class IdentityProof {
 record Pin(String id,String key,PublicKey publicKey){}
 static Pin pin(String id,String encoded,String algorithm)throws Exception{
  if(!"p256-sha256".equals(algorithm)||id==null||!id.matches("[0-9a-f]{64}")||encoded==null||encoded.length()>2048)throw new GeneralSecurityException("服务身份格式无效");
  byte[] der=Base64.getDecoder().decode(encoded);PublicKey key=KeyFactory.getInstance("EC").generatePublic(new X509EncodedKeySpec(der));if(!(key instanceof ECPublicKey ec)||!Arrays.equals(der,key.getEncoded()))throw new GeneralSecurityException("公钥无效");
  AlgorithmParameters params=AlgorithmParameters.getInstance("EC");params.init(new ECGenParameterSpec("secp256r1"));ECParameterSpec p=params.getParameterSpec(ECParameterSpec.class),q=ec.getParams();
  if(!p.getOrder().equals(q.getOrder())||!p.getGenerator().equals(q.getGenerator())||!p.getCurve().equals(q.getCurve())||p.getCofactor()!=q.getCofactor())throw new GeneralSecurityException("需要 P-256 公钥");
  String actual=hex(MessageDigest.getInstance("SHA-256").digest(der));if(!actual.equals(id))throw new GeneralSecurityException("服务公钥指纹不匹配");return new Pin(id,Base64.getEncoder().encodeToString(der),key);
 }
 static void verify(Pin pin,String nonce,String origin,int protocol,String id,String key,String responseNonce,String responseOrigin,String signature)throws Exception{
  if(nonce==null||!nonce.matches("[0-9a-f]{64}")||protocol!=1||!nonce.equals(responseNonce)||!origin.equals(responseOrigin)||!pin.id.equals(id)||signature==null||signature.length()>512)throw new GeneralSecurityException("服务身份响应不匹配");
  Pin response=pin(id,key,"p256-sha256");if(!response.key.equals(pin.key))throw new GeneralSecurityException("服务公钥已变化");
  Signature verifier=Signature.getInstance("SHA256withECDSA");verifier.initVerify(pin.publicKey);verifier.update(message(nonce,id,origin));if(!verifier.verify(Base64.getDecoder().decode(signature)))throw new GeneralSecurityException("服务身份签名无效");
 }
 static byte[] message(String nonce,String id,String origin){return ("codex-pocket-route-v1\n"+nonce+"\n"+id+"\n"+origin).getBytes(StandardCharsets.UTF_8);}
 static String nonce(){byte[] b=new byte[32];new SecureRandom().nextBytes(b);return hex(b);}
 static String hex(byte[] bytes){StringBuilder b=new StringBuilder();for(byte value:bytes)b.append(String.format(Locale.ROOT,"%02x",value&255));return b.toString();}
}
