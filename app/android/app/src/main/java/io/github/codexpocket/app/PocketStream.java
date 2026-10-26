package io.github.codexpocket.app;
import org.json.JSONObject;
import java.io.*;
import java.net.HttpURLConnection;
import java.nio.charset.StandardCharsets;
final class PocketStream {
 interface Listener{void event(JSONObject value);}
 private volatile long wakeGeneration;
 private volatile boolean running;private volatile long generation;private volatile HttpURLConnection connection;private Thread thread;
 synchronized void start(NetworkRoutes routes,JSONObject auth,Listener listener){
  stop();running=true;final long mine=++generation;
  thread=new Thread(()->{long revision=0,backoff=1000;while(running&&mine==generation){
   long wake=wakeGeneration;int receivedStatus=-1;HttpURLConnection owned=null;NetworkRoutes.Selection selected=null;try{
    selected=routes.currentSelection(auth);HttpURLConnection c=ApiClient.open(selected.url(),"/api/events?after="+revision,auth.optString("token"),selected.tlsPin());owned=c;connection=c;c.setConnectTimeout(3500);c.setReadTimeout(45000);c.setRequestProperty("Accept","text/event-stream");int status=c.getResponseCode();receivedStatus=status;if(!running||mine!=generation)break;
    if(!routes.selectionCurrent(selected,auth))continue;
    if(status==401){byte[] bytes=c.getErrorStream()==null?new byte[0]:ApiClient.read(c.getErrorStream(),8192);if(NetworkRoutes.revoked(new ApiClient.Result(status,bytes,"application/json"))){emit(listener,"unauthorized",revision,mine);break;}throw new IOException("线路额外认证不可用");}
    if(status!=200)throw new IOException("SSE status");emit(listener,"connected",revision,mine);backoff=1000;
    try(BufferedReader r=new BufferedReader(new InputStreamReader(c.getInputStream(),StandardCharsets.UTF_8))){String line;while(running&&mine==generation&&(line=r.readLine())!=null){if(!routes.selectionCurrent(selected,auth))break;if(line.length()>32768)throw new IOException("event too large");if(line.startsWith("id:")){try{revision=Long.parseLong(line.substring(3).trim());}catch(NumberFormatException ignored){}}if(line.startsWith("data:")){JSONObject data=new JSONObject(line.substring(5).trim());revision=Math.max(revision,data.optLong("revision",revision));routes.discoveryHint(auth,data.optLong("discovery_revision",-1));emit(listener,"update",revision,mine);}}}
   }catch(Exception ignored){if(running&&mine==generation){if(ignored instanceof IOException&&StreamFailurePolicy.shouldProbe(receivedStatus,wake==wakeGeneration,running&&mine==generation)&&selected!=null&&routes.selectionCurrent(selected,auth))routes.requestProbe(auth);emit(listener,"disconnected",revision,mine);}}
   finally{if(owned!=null)owned.disconnect();if(connection==owned)connection=null;}
   if(running&&mine==generation){try{Thread.sleep(backoff);}catch(InterruptedException ignored){}backoff=StreamFailurePolicy.nextBackoff(backoff);}
  }},"pocket-events");thread.setDaemon(true);thread.start();
 }
 synchronized void nudge(){wakeGeneration++;HttpURLConnection c=connection;if(c!=null)c.disconnect();if(thread!=null)thread.interrupt();}
 private void emit(Listener l,String type,long seq,long mine){if(!running||mine!=generation)return;try{l.event(new JSONObject().put("type",type).put("revision",seq));}catch(Exception ignored){}}
 synchronized void stop(){wakeGeneration++;running=false;generation++;HttpURLConnection c=connection;if(c!=null)c.disconnect();if(thread!=null)thread.interrupt();}
}
