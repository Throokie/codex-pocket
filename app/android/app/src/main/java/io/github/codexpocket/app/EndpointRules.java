package io.github.codexpocket.app;
import org.json.*;
import java.util.*;
final class EndpointRules {
 static JSONArray normalize(JSONArray input,String origin,boolean ensureOrigin,boolean debug)throws Exception{
  if(input!=null&&input.length()>6)throw new IllegalArgumentException("最多保存6条线路");LinkedHashMap<String,JSONObject> values=new LinkedHashMap<>();
  if(input!=null)for(int i=0;i<input.length();i++){JSONObject p=input.getJSONObject(i);String url=OriginRules.normalize(p.getString("url"),debug);String label=p.optString("label","").trim(),kind=p.optString("kind","private");if(label.codePointCount(0,label.length())>40||!Set.of("lan","tailscale","private","public").contains(kind))throw new IllegalArgumentException("线路名称或类型无效");if(values.containsKey(url))throw new IllegalArgumentException("线路地址重复");JSONObject normalized=new JSONObject().put("url",url).put("label",label.isEmpty()?url:label).put("kind",kind).put("tls",p.optString("tls","system")).put("managed",p.optBoolean("managed",false)).put("source",p.optString("source","manual"));DiscoveryManifest.validateEndpoint(normalized);values.put(url,normalized);}
  if(ensureOrigin&&!values.containsKey(origin)){if(values.size()>=6)throw new IllegalArgumentException("6条线路中必须包含原配对地址");values.put(origin,new JSONObject().put("url",origin).put("label","配对地址").put("kind",origin.startsWith("https://")?"public":"private"));}
  if(values.isEmpty())throw new IllegalArgumentException("至少保留1条线路");JSONArray out=new JSONArray();for(JSONObject p:values.values())out.put(p);return out;
 }
}
