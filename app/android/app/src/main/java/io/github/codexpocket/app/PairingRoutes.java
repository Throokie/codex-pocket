package io.github.codexpocket.app;
import org.json.*;
import java.io.IOException;
import java.util.*;
import java.util.concurrent.*;
/** Probe only: no ticket, polling credential or bearer is sent by this chooser. */
final class PairingRoutes {
 static String choose(String logical,IdentityProof.Pin identity,JSONObject discovery,JSONArray endpoints,ApiClient.Scope scope)throws Exception{
  if(identity==null)return logical;JSONObject grant=new JSONObject().put("discovery",discovery).put("endpoints",endpoints);LinkedHashSet<String> origins=new LinkedHashSet<>();origins.add(logical);if(endpoints!=null)for(int i=0;i<endpoints.length();i++)origins.add(endpoints.getJSONObject(i).getString("url"));
  if(origins.stream().anyMatch(u->u.startsWith("https://")))origins.removeIf(u->!u.startsWith("https://"));
  return VerifiedCandidates.choose(origins,origin->NetworkRoutes.verifyOrigin(origin,identity,scope,DiscoveryManifest.tlsPin(grant,origin)),4500);
 }
}
