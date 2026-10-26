package io.github.codexpocket.app;
import org.json.*;
public final class DiscoveryManifestTest {
 static int tests;interface Work{void run()throws Exception;}static void check(boolean value,String label){if(!value)throw new AssertionError(label);tests++;System.out.println("PASS "+label);}static void reject(Work w,String label)throws Exception{try{w.run();}catch(Exception expected){check(true,label);return;}throw new AssertionError(label);}
 static JSONObject endpoint(String ip,boolean managed)throws Exception{return new JSONObject().put("url","https://10.0.0."+ip+":32851").put("label","computer").put("kind","lan").put("tls",managed?"pinned":"system").put("managed",managed).put("source",managed?"auto":"manual");}
 public static void main(String[] args)throws Exception{
  check(DiscoveryManifest.config(null,null)==null,"legacy QR has no discovery grant");
  reject(()->DiscoveryManifest.config(new JSONObject().put("version",1).put("enabled",true).put("service_type",DiscoveryManifest.TYPE).put("tls_spki","0".repeat(64)).put("revision",1),null),"TLS pin without QR identity cannot enable discovery");
  reject(()->DiscoveryManifest.revision(new JSONObject().put("revision",1.5)),"fractional revision rejected");
  JSONObject old=endpoint("1",true),next=endpoint("2",true),extra=endpoint("3",false);JSONArray merged=DiscoveryManifest.merge(new JSONArray().put(next),new JSONArray().put(old).put(extra));
  check(merged.length()==2&&merged.getJSONObject(0).getString("url").contains("10.0.0.2")&&merged.getJSONObject(1).getString("url").contains("10.0.0.3"),"managed migration removes old and keeps manual extra");
  JSONArray current=new JSONArray().put(next).put(extra);JSONObject forged=new JSONObject(next.toString()).put("managed",false).put("label","spoof");JSONArray configured=DiscoveryManifest.userConfig(new JSONArray().put(forged).put(extra),current);check(configured.getJSONObject(0).getBoolean("managed")&&configured.getJSONObject(0).getString("tls").equals("pinned")&&configured.getJSONObject(0).getString("label").equals("computer"),"JS cannot rewrite managed authority");
  reject(()->DiscoveryManifest.userConfig(new JSONArray().put(extra),current),"JS cannot delete managed endpoint");
  reject(()->DiscoveryManifest.userConfig(new JSONArray().put(next).put(endpoint("4",true)),current),"JS cannot introduce managed endpoint");
  reject(()->DiscoveryManifest.userConfig(new JSONArray().put(next).put(endpoint("4",false).put("tls","pinned")),current),"JS cannot introduce pinned endpoint");
  JSONArray many=new JSONArray();for(int i=10;i<16;i++)many.put(endpoint(""+i,true));reject(()->DiscoveryManifest.merge(many,new JSONArray().put(extra)),"overflow rejected without dropping extras");
  reject(()->DiscoveryManifest.signedEndpoints(new JSONArray().put(extra)),"signed list requires managed authority");
  reject(()->DiscoveryManifest.signedEndpoints(new JSONArray().put(endpoint("4",true).put("url","https://example.com"))),"pinned route restricted private or TS");
  reject(()->DiscoveryManifest.signedEndpoints(new JSONArray().put(endpoint("4",true).put("tls","system"))),"auto source cannot downgrade TLS");
  check(DiscoveryManifest.signedEndpoints(new JSONArray().put(endpoint("4",true))).getJSONObject(0).getBoolean("managed"),"signed managed endpoint accepted");
  System.out.println("PASS TOTAL "+tests);
 }
}
