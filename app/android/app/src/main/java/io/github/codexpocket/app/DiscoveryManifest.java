package io.github.codexpocket.app;
import org.json.*;
import java.net.URI;
import java.security.GeneralSecurityException;
import java.util.*;
final class DiscoveryManifest {
 static final String TYPE="_codexpocket._tcp.";
 static long revision(JSONObject value)throws Exception{Object raw=value.get("revision");if(!(raw instanceof Integer||raw instanceof Long)||((Number)raw).longValue()<0)throw new GeneralSecurityException("自动发现版本必须为非负整数");return ((Number)raw).longValue();}
 static JSONObject config(JSONObject input,IdentityProof.Pin identity)throws Exception{
  if(input==null)return null;
  if(identity==null||input.optInt("version")!=1||!input.optBoolean("enabled")||!TYPE.equals(input.optString("service_type"))||!input.optString("tls_spki").matches("[0-9a-f]{64}")||revision(input)<0)throw new GeneralSecurityException("自动发现配对配置无效");
  return new JSONObject().put("version",1).put("enabled",true).put("tls_spki",input.getString("tls_spki")).put("service_type",TYPE).put("revision",revision(input));
 }
 static String tlsPin(JSONObject grant,String origin)throws Exception{
  if(grant==null)return null;JSONObject discovery=grant.optJSONObject("discovery");if(discovery==null)return null;
  JSONArray endpoints=grant.optJSONArray("endpoints");if(endpoints!=null)for(int i=0;i<endpoints.length();i++){JSONObject e=endpoints.getJSONObject(i);if(origin.equals(e.optString("url"))&&e.optString("tls","system").equals("pinned"))return discovery.getString("tls_spki");}return null;
 }
 static void validateEndpoint(JSONObject e)throws Exception{
  String tls=e.optString("tls","system");if(e.optString("source","manual").equals("auto")&&!tls.equals("pinned"))throw new GeneralSecurityException("自动线路必须使用绑定TLS证书");if(!Set.of("pinned","system").contains(tls))throw new GeneralSecurityException("TLS策略无效");
  if(tls.equals("pinned")){URI u=URI.create(e.getString("url"));String host=u.getHost();if(host.startsWith("["))host=host.substring(1,host.length()-1);if(!"https".equals(u.getScheme())||!(OriginRules.privateHost(host)||host.endsWith(".ts.net")))throw new GeneralSecurityException("绑定证书仅支持私网或Tailscale地址");}
 }
 static JSONArray signedEndpoints(JSONArray raw)throws Exception{
  JSONArray list=EndpointRules.normalize(raw,"",false,BuildConfig.DEBUG);
  for(int i=0;i<list.length();i++){JSONObject e=list.getJSONObject(i);if(!e.optBoolean("managed")||!Set.of("auto","manual").contains(e.optString("source")))throw new GeneralSecurityException("自动发现来源无效");validateEndpoint(e);}return list;
 }
 static JSONArray merge(JSONArray managed,JSONArray current)throws Exception{
  LinkedHashMap<String,JSONObject> values=new LinkedHashMap<>();for(int i=0;i<managed.length();i++){JSONObject e=managed.getJSONObject(i);values.put(e.getString("url"),e);}
  if(current!=null)for(int i=0;i<current.length();i++){JSONObject e=current.getJSONObject(i);if(!e.optBoolean("managed")&&!values.containsKey(e.getString("url")))values.put(e.getString("url"),e);}
  if(values.size()>6)throw new IllegalArgumentException("电脑管理线路与手机线路合计超过6条，请先删除手机额外线路");JSONArray out=new JSONArray();for(JSONObject e:values.values())out.put(e);return out;
 }
 static JSONArray userConfig(JSONArray requested,JSONArray current)throws Exception{
  JSONArray normalized=EndpointRules.normalize(requested,"",false,BuildConfig.DEBUG),out=new JSONArray();Map<String,JSONObject> trusted=new HashMap<>();if(current!=null)for(int i=0;i<current.length();i++){JSONObject e=current.getJSONObject(i);if(e.optBoolean("managed"))trusted.put(e.getString("url"),e);}
  Set<String> seen=new HashSet<>();for(int i=0;i<normalized.length();i++){JSONObject e=normalized.getJSONObject(i);String url=e.getString("url");seen.add(url);JSONObject original=trusted.get(url);if(original!=null)out.put(new JSONObject(original.toString()));else{if(e.optBoolean("managed")||e.optString("tls","system").equals("pinned"))throw new GeneralSecurityException("手机不能新增托管线路或证书绑定");e.put("managed",false).put("source","manual").put("tls","system");out.put(e);}}
  for(String url:trusted.keySet())if(!seen.contains(url))throw new GeneralSecurityException("电脑管理线路只能在电脑修改");return out;
 }
}
