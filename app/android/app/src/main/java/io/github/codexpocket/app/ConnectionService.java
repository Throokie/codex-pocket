package io.github.codexpocket.app;

import android.app.*;
import android.content.*;
import android.os.*;
import android.content.pm.PackageManager;
import org.json.*;
import java.util.*;
import java.util.concurrent.*;

public final class ConnectionService extends Service {
    static volatile PocketStream.Listener listener;
    static volatile boolean active=false;
    private NetworkRoutes routes;private long streamRouteGeneration=-1;
    private final PocketStream stream=new PocketStream();
    private final ExecutorService executor=Executors.newSingleThreadExecutor();
    private final Set<String> seen=new LinkedHashSet<>();
    private final Object notificationLock=new Object();
    private volatile long connectionGeneration=0;
    private JSONObject activeAuth;
    private final CoalescingTask refresh=new CoalescingTask(executor,()->{JSONObject auth;long generation;synchronized(notificationLock){auth=activeAuth;generation=connectionGeneration;}if(auth!=null)notifyPending(auth,generation);});
    static final String CHANNEL="pocket_requests",CONNECTION="pocket_connection";
    static void channels(Context c){if(Build.VERSION.SDK_INT>=26){NotificationManager nm=c.getSystemService(NotificationManager.class);nm.createNotificationChannel(new NotificationChannel(CHANNEL,"需要你处理",NotificationManager.IMPORTANCE_DEFAULT));nm.createNotificationChannel(new NotificationChannel(CONNECTION,"后台连接",NotificationManager.IMPORTANCE_LOW));}}
    static PendingIntent openIntent(Context c){return PendingIntent.getActivity(c,0,new Intent(c,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
    @Override public void onCreate(){super.onCreate();routes=NetworkRoutes.get(this);channels(this);}
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        JSONObject auth=new Credentials(this).read();if(auth==null){stopSelf();return START_NOT_STICKY;}
        synchronized(notificationLock){connectionGeneration++;activeAuth=auth;seen.clear();}
        startForeground(100,new Notification.Builder(this,CONNECTION).setSmallIcon(R.drawable.ic_pocket).setContentTitle("Codex Pocket 后台连接").setContentText("保持会话提醒连接；系统仍可能暂停网络").setContentIntent(openIntent(this)).setOngoing(true).build());active=true;
        routes.attach(this,false,state->{long next=state.optLong("route_generation",-1);if(next!=streamRouteGeneration){streamRouteGeneration=next;stream.nudge();}});
        stream.start(routes,auth,event->{Credentials store=new Credentials(this);if(!store.matches(auth))return;long expected=auth.optLong("_epoch",-1);if("unauthorized".equals(event.optString("type"))){expected=store.invalidateIfMatches(auth,()->stopSelf(startId));if(expected<0)return;}if(store.epoch()!=expected)return;try{event.put("_connection_epoch",expected);}catch(JSONException ignored){}PocketStream.Listener l=listener;if(l!=null)l.event(event);if("update".equals(event.optString("type")))scheduleRefresh();});
        return START_NOT_STICKY;
    }
    private void scheduleRefresh(){refresh.trigger();}
    private void notifyPending(JSONObject auth,long generation){
        try{
            ApiClient.Result r=routes.call(auth,"/api/state","GET",null,null,4*1024*1024,null);if(r.status()!=200||generation!=connectionGeneration||!new Credentials(this).matches(auth))return;
            JSONArray requests=new JSONObject(r.text()).optJSONArray("requests");if(requests==null)return;int fresh=0;
            synchronized(notificationLock){if(generation!=connectionGeneration||!new Credentials(this).matches(auth))return;
                for(int i=0;i<requests.length();i++){JSONObject item=requests.getJSONObject(i);String id=item.optString("id");if(id.isEmpty()||!"pending".equals(item.optString("status","pending")))continue;if(seen.add(id))fresh++;}
                while(seen.size()>200)seen.remove(seen.iterator().next());
            }if(fresh==0)return;
            if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
            synchronized(notificationLock){if(generation!=connectionGeneration||!new Credentials(this).matches(auth))return;
            new Credentials(this).runIfMatches(auth,()->getSystemService(NotificationManager.class).notify(101,new Notification.Builder(this,CHANNEL).setSmallIcon(R.drawable.ic_pocket).setContentTitle("有会话需要你处理").setContentText("打开 Codex Pocket 查看原问题和选项").setContentIntent(openIntent(this)).setAutoCancel(true).build()));}
        }catch(Exception ignored){}
    }
    @Override public void onDestroy(){routes.detach(this);synchronized(notificationLock){connectionGeneration++;activeAuth=null;refresh.close();}active=false;stream.stop();executor.shutdownNow();stopForeground(STOP_FOREGROUND_REMOVE);super.onDestroy();}
    @Override public void onTimeout(int startId,int fgsType){stopSelf();}
    @Override public IBinder onBind(Intent intent){return null;}
}
