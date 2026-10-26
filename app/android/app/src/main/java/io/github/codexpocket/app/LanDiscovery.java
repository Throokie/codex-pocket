package io.github.codexpocket.app;
import android.content.Context;
import android.net.nsd.*;
import android.net.wifi.WifiManager;
import android.os.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
/** Bounded NSD hints, never authenticated routes. Every result still needs pinned TLS + signed manifest. */
final class LanDiscovery {
 interface Listener{void candidate(String origin);void finished(String reason);}
 private final WifiManager.MulticastLock multicast;private final NsdManager manager;private final Handler main=new Handler(Looper.getMainLooper());
 private NsdManager.DiscoveryListener discovery;private long generation,lastStart;private String lastId="";private final ArrayDeque<NsdServiceInfo> queue=new ArrayDeque<>();private final Set<String> seen=new HashSet<>();private boolean resolving;
 LanDiscovery(Context c){manager=(NsdManager)c.getSystemService(Context.NSD_SERVICE);WifiManager wifi=(WifiManager)c.getApplicationContext().getSystemService(Context.WIFI_SERVICE);multicast=wifi==null?null:wifi.createMulticastLock("codex-pocket-discovery");if(multicast!=null)multicast.setReferenceCounted(false);}
 void start(String id,Listener listener){main.post(()->startMain(id,listener));}
 private void startMain(String id,Listener listener){if(!lastId.equals(id)){stopMain();lastStart=0;lastId=id;}if(discovery!=null||System.currentTimeMillis()-lastStart<60000)return;lastStart=System.currentTimeMillis();try{if(multicast!=null)multicast.acquire();}catch(Exception ignored){}final long mine=++generation;queue.clear();seen.clear();resolving=false;
  discovery=new NsdManager.DiscoveryListener(){public void onDiscoveryStarted(String type){}public void onDiscoveryStopped(String type){}public void onServiceLost(NsdServiceInfo info){}public void onStopDiscoveryFailed(String type,int code){}public void onStartDiscoveryFailed(String type,int code){finish(mine,listener,"局域网发现不可用");}public void onServiceFound(NsdServiceInfo info){main.post(()->{if(mine!=generation||discovery==null||seen.size()>=8)return;if(!info.getServiceType().contains("_codexpocket._tcp"))return;if(!seen.add(info.getServiceName()))return;queue.add(info);resolveNext(mine,id,listener);});}};
  try{manager.discoverServices(DiscoveryManifest.TYPE,NsdManager.PROTOCOL_DNS_SD,discovery);main.postDelayed(()->finish(mine,listener,"未找到同一电脑；路由器可能隔离局域网发现，可检查Tailscale或重新扫码"),25000);}catch(Exception e){finish(mine,listener,"局域网发现不可用");}
 }
 private void resolveNext(long mine,String id,Listener listener){if(mine!=generation||resolving||queue.isEmpty()||discovery==null)return;resolving=true;NsdServiceInfo info=queue.remove();try{manager.resolveService(info,new NsdManager.ResolveListener(){public void onResolveFailed(NsdServiceInfo service,int code){main.post(()->{if(mine!=generation)return;resolving=false;resolveNext(mine,id,listener);});}public void onServiceResolved(NsdServiceInfo service){main.post(()->{if(mine!=generation||discovery==null)return;resolving=false;try{Map<String,byte[]> txt=service.getAttributes();if(id.equals(txt(txt,"id"))&&"1".equals(txt(txt,"v"))){InetAddress address=service.getHost();int port=service.getPort();String declared=txt(txt,"tlsport");if(!declared.isEmpty()&&Integer.parseInt(declared)!=port)throw new IllegalArgumentException();if(address!=null&&!address.isAnyLocalAddress()&&!address.isMulticastAddress()&&port>0&&port<=65535){String host=address.getHostAddress();if(host!=null&&!host.contains("%")&&OriginRules.privateHost(host)){String origin=ApiClient.origin("https://"+(host.contains(":")?"["+host+"]":host)+":"+port);listener.candidate(origin);}}}}catch(Exception ignored){}resolveNext(mine,id,listener);});}});}catch(Exception e){resolving=false;resolveNext(mine,id,listener);}}
 private static String txt(Map<String,byte[]> values,String key){byte[] value=values.get(key);return value==null||value.length>256?"":new String(value,StandardCharsets.UTF_8);}
 private void finish(long mine,Listener listener,String reason){if(mine!=generation)return;stopMain();listener.finished(reason);}
 void stop(){main.post(this::stopMain);}
 private void stopMain(){generation++;if(multicast!=null&&multicast.isHeld())try{multicast.release();}catch(Exception ignored){}NsdManager.DiscoveryListener old=discovery;discovery=null;queue.clear();resolving=false;if(old!=null)try{manager.stopServiceDiscovery(old);}catch(Exception ignored){}}
}
