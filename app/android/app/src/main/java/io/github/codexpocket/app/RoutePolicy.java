package io.github.codexpocket.app;
import java.util.*;
final class RoutePolicy {
 record View(String url,boolean available,double latency,long cooldownUntil){}
 static String choose(List<View> routes,String mode,String manual,String active,long now,long lastSwitch){
  if(mode.equals("manual")){for(View r:routes)if(r.url.equals(manual)&&r.available)return r.url;return "";}
  boolean secure=routes.stream().anyMatch(r->r.url.startsWith("https://"));View best=null,current=null,available=null;
  for(View r:routes){if(!r.available||(secure&&!r.url.startsWith("https://")))continue;if(available==null||r.latency<available.latency)available=r;if(r.url.equals(active))current=r;if(now<r.cooldownUntil&&!r.url.equals(active))continue;if(best==null||r.latency<best.latency)best=r;}
  if(current==null)return best!=null?best.url:available==null?"":available.url;if(best==null||best.url.equals(current.url))return current.url;
  if(now-lastSwitch<20000||best.latency>current.latency*.80||current.latency-best.latency<40)return current.url;return best.url;
 }
 static final class Gate {
  private String candidate="";private int rounds;
  String apply(List<View> views,String mode,String desired,String active,boolean completed){
   boolean secure=views.stream().anyMatch(v->v.url.startsWith("https://"));View old=views.stream().filter(v->v.url.equals(active)).findFirst().orElse(null);
   boolean healthy=old!=null&&old.available&&(!secure||active.startsWith("https://"));
   if(mode.equals("auto")&&!desired.isEmpty()&&!desired.equals(active)&&healthy){if(completed){if(desired.equals(candidate))rounds++;else{candidate=desired;rounds=1;}}if(!desired.equals(candidate)||rounds<2)return active;}
   else if(completed){candidate="";rounds=0;}
   if(!desired.equals(active)){candidate="";rounds=0;}return desired;
  }
 }
 static double ewma(double previous,double measured){return previous<0?measured:previous*.70+measured*.30;}
}
