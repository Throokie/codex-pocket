package io.github.codexpocket.app;

import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;

final class ApiClient {
    static final class Scope {
        private final java.util.Set<HttpURLConnection> active=java.util.concurrent.ConcurrentHashMap.newKeySet();
        private volatile boolean closed;
        synchronized void attach(HttpURLConnection c)throws IOException{if(closed){c.disconnect();throw new IOException("stale connection");}active.add(c);}
        boolean isClosed(){return closed;}
        void detach(HttpURLConnection c){active.remove(c);}
        synchronized void close(){closed=true;for(HttpURLConnection c:active)c.disconnect();active.clear();}
    }
    static final int MAX_MEDIA=15*1024*1024;
    record Result(int status,byte[] bytes,String mime,long routeEpoch,String routeUrl){Result(int status,byte[] bytes,String mime){this(status,bytes,mime,-1,"");}Result route(long epoch,String url){return new Result(status,bytes,mime,epoch,url);}String text(){return new String(bytes,StandardCharsets.UTF_8);}}
    static String origin(String value)throws Exception{return OriginRules.normalize(value,BuildConfig.DEBUG);}
    static boolean allowed(String method,String path){
        if(path==null||path.length()>400||path.contains("..")||path.contains("%")||path.contains("\\")||path.contains("#"))return false;
        return switch(method){
            case "GET" -> path.equals("/health")||path.equals("/api/state")||path.equals("/api/devices")||path.matches("/api/media/[A-Za-z0-9_-]{1,128}");
            case "POST" -> path.equals("/pair/claim")||path.equals("/pair/status")||path.equals("/api/messages")||path.equals("/api/controller")||path.equals("/api/settings")||path.equals("/api/media")||path.matches("/api/requests/[A-Za-z0-9_-]{1,128}/answer");
            case "DELETE" -> path.equals("/api/cache")||path.matches("/api/devices/[A-Za-z0-9_-]{1,128}");
            default -> false;
        };
    }
    static HttpURLConnection open(String base,String path,String token) throws Exception {return open(base,path,token,null);}
    static HttpURLConnection open(String base,String path,String token,String tlsPin) throws Exception {
        URL target=new URL(base+path);HttpURLConnection c=(HttpURLConnection)target.openConnection();
        if(tlsPin!=null){if(!(c instanceof javax.net.ssl.HttpsURLConnection https))throw new java.security.GeneralSecurityException("Pinned TLS requires HTTPS");PinnedTls.apply(https,tlsPin);}
        c.setInstanceFollowRedirects(false);c.setConnectTimeout(10000);c.setReadTimeout(40000);
        c.setRequestProperty("Accept","application/json");c.setRequestProperty("User-Agent","CodexPocket/0.4.0 Android (unofficial)");
        if(token!=null&&!token.isEmpty())c.setRequestProperty("Authorization","Bearer "+token);
        return c;
    }
    static Result call(String base,String path,String token,String method,byte[] body,String mime,int limit)throws Exception{return call(base,path,token,method,body,mime,limit,null);}
    static Result call(String base,String path,String token,String method,byte[] body,String mime,int limit,Scope scope)throws Exception{return call(base,path,token,method,body,mime,limit,scope,null);}
    static Result call(String base,String path,String token,String method,byte[] body,String mime,int limit,Scope scope,Scope routeScope)throws Exception{return call(base,path,token,method,body,mime,limit,scope,routeScope,null);}
    static Result call(String base,String path,String token,String method,byte[] body,String mime,int limit,Scope scope,Scope routeScope,String tlsPin)throws Exception{
        if(!allowed(method,path))throw new IllegalArgumentException("该接口未开放");
        HttpURLConnection c=open(base,path,token,tlsPin);
        try{if(scope!=null)scope.attach(c);if(routeScope!=null)routeScope.attach(c);
            c.setRequestMethod(method);
            if(body!=null){c.setDoOutput(true);c.setRequestProperty("Content-Type",mime);c.setFixedLengthStreamingMode(body.length);try(OutputStream out=c.getOutputStream()){out.write(body);}}
            int status=c.getResponseCode();InputStream in=status<400?c.getInputStream():c.getErrorStream();byte[] bytes=in==null?new byte[0]:read(in,limit);
            if(status>=300&&status<400)return new Result(502,error("redirect_rejected","服务端重定向已拒绝，请使用最终服务地址").toString().getBytes(StandardCharsets.UTF_8),"application/json");
            return new Result(status,bytes,c.getContentType());
        }finally{c.disconnect();if(scope!=null)scope.detach(c);if(routeScope!=null)routeScope.detach(c);}
    }
    static byte[] read(InputStream in,int limit)throws Exception{
        try(in;ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] buf=new byte[16384];int n,total=0;while((n=in.read(buf))!=-1){total+=n;if(total>limit)throw new IOException("响应超出大小限制");out.write(buf,0,n);}return out.toByteArray();}
    }
    static JSONObject error(String code,String message){try{return new JSONObject().put("error",new JSONObject().put("code",code).put("message",message));}catch(Exception e){throw new IllegalStateException(e);}}
}
