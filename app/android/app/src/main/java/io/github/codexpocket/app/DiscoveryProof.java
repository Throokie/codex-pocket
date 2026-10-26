package io.github.codexpocket.app;
import java.security.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
/** Exact UTF-8 payload is signed; it is never reserialized before verification. */
final class DiscoveryProof {
 static void verify(IdentityProof.Pin pin,String nonce,int protocol,String id,String replyNonce,String payload,String signature)throws Exception{
  if(pin==null||nonce==null||!nonce.matches("[0-9a-f]{64}")||protocol!=1||!pin.id().equals(id)||!nonce.equals(replyNonce)||payload==null||payload.getBytes(StandardCharsets.UTF_8).length>16384||signature==null||signature.length()>512)throw new GeneralSecurityException("自动发现签名响应无效");
  Signature verifier=Signature.getInstance("SHA256withECDSA");verifier.initVerify(pin.publicKey());verifier.update(message(nonce,id,payload));if(!verifier.verify(Base64.getDecoder().decode(signature)))throw new GeneralSecurityException("自动发现签名不匹配");
 }
 static void verifyConfig(IdentityProof.Pin pin,String nonce,int protocol,String id,String replyNonce,String payload,String signature,boolean pairing)throws Exception{
  if(pin==null||nonce==null||!nonce.matches("[0-9a-f]{64}")||protocol!=1||!pin.id().equals(id)||!nonce.equals(replyNonce)||payload==null||payload.getBytes(StandardCharsets.UTF_8).length>16384||signature==null||signature.length()>512)throw new GeneralSecurityException("配置签名响应无效");Signature verifier=Signature.getInstance("SHA256withECDSA");verifier.initVerify(pin.publicKey());String domain=pairing?"codex-pocket-config-pair-v1":"codex-pocket-config-v1";verifier.update((domain+"\n"+nonce+"\n"+id+"\n"+payload).getBytes(StandardCharsets.UTF_8));if(!verifier.verify(Base64.getDecoder().decode(signature)))throw new GeneralSecurityException("配置签名不匹配");
 }
 static boolean current(boolean grantMatches,long expectedGeneration,long currentGeneration){return grantMatches&&expectedGeneration==currentGeneration;}
 static byte[] message(String nonce,String id,String payload){return ("codex-pocket-discovery-v1\n"+nonce+"\n"+id+"\n"+payload).getBytes(StandardCharsets.UTF_8);}
 static String hash(String payload)throws Exception{return IdentityProof.hex(MessageDigest.getInstance("SHA-256").digest(payload.getBytes(StandardCharsets.UTF_8)));}
 static void revision(long previous,String previousHash,long next,String hash)throws GeneralSecurityException{if(next<0||next<previous||(next==previous&&previousHash!=null&&!previousHash.isEmpty()&&!previousHash.equals(hash)))throw new GeneralSecurityException("自动发现版本过期或内容冲突");}
}
