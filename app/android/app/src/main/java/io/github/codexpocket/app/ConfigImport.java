package io.github.codexpocket.app;
import org.json.*;
import java.io.*;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;
/** Explicit user action only. Configuration endpoints never receive a bearer or existing polling credential. */
final class ConfigImport {
 static String choose(JSONObject config,ApiClient.Scope scope)throws Exception{return PairingRoutes.choose(config.getString("url"),NetworkRoutes.parsePin(config.getJSONObject("server_identity")),config.getJSONObject("discovery"),config.getJSONArray("endpoints"),scope);}
 static JSONObject pairing(JSONObject config,String origin,ApiClient.Scope scope)throws Exception{
  String nonce=IdentityProof.nonce();String tlsPin=config.getJSONObject("discovery").getString("tls_spki");HttpURLConnection c=ApiClient.open(origin,"/config/"+config.getString("config_id")+"/pair",null,tlsPin);c.setConnectTimeout(3500);c.setReadTimeout(7000);c.setRequestMethod("POST");c.setRequestProperty("Content-Type","application/json");byte[] body=new JSONObject().put("challenge",nonce).toString().getBytes(StandardCharsets.UTF_8);c.setDoOutput(true);c.setFixedLengthStreamingMode(body.length);scope.attach(c);
  try{try(OutputStream out=c.getOutputStream()){out.write(body);}int status=c.getResponseCode();if(status==404||status==410)throw new IOException("config_revoked");if(status!=200)throw new IOException("config_request_failed");String raw=StrictJson.utf8(ApiClient.read(c.getInputStream(),24576));JSONObject wrapper=StrictJson.object(raw,24576);if(!(wrapper.get("protocol") instanceof Integer protocol)||protocol!=1)throw new java.security.GeneralSecurityException("配置协议无效");String payload=ConfigLinks.string(wrapper,"payload");DiscoveryProof.verifyConfig(NetworkRoutes.parsePin(config.getJSONObject("server_identity")),nonce,protocol,ConfigLinks.string(wrapper,"server_id"),ConfigLinks.string(wrapper,"challenge"),payload,ConfigLinks.string(wrapper,"signature"),true);JSONObject pairing=StrictJson.object(payload,16384);ConfigLinks.validatePairing(pairing,config);return pairing;}finally{c.disconnect();scope.detach(c);}
 }
}
