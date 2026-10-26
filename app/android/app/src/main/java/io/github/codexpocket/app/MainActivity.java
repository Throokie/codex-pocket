package io.github.codexpocket.app;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.webkit.*;
import android.util.Base64;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;
import org.json.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

/** Bundled UI only. No bridge, tokens or remote HTML are exposed to external pages. */
public final class MainActivity extends Activity {
    private static final String LOCAL="https://appassets.androidplatform.net";
    private static final int FILE_REQUEST=91,NOTIFY_REQUEST=92;
    private final Object configLock=new Object();private volatile long configGeneration;private long configPreparedGeneration=-1;private volatile boolean configBusy;private ApiClient.Scope configScope=new ApiClient.Scope();
    private final ExecutorService configIo=new ThreadPoolExecutor(1,1,0,TimeUnit.MILLISECONDS,new ArrayBlockingQueue<>(2));
    private WebView web;
    private Credentials credentials;private NetworkRoutes routes;
    private final ExecutorService routeControl=new ThreadPoolExecutor(1,1,0,TimeUnit.MILLISECONDS,new ArrayBlockingQueue<>(2));
    private volatile boolean activityForeground=false;private volatile long streamRouteGeneration=-1;
    private final ExecutorService io=Executors.newFixedThreadPool(3);
    private final ExecutorService pairingIo=Executors.newFixedThreadPool(2);
    private volatile ApiClient.Scope apiScope=new ApiClient.Scope();
    private volatile ApiClient.Scope pairingScope=new ApiClient.Scope();
    private final PocketStream stream=new PocketStream();
    private ValueCallback<Uri[]> fileCallback;
    private volatile String candidateOrigin="";
    private volatile String candidateDeviceName="";
    private final Object pairingLock=new Object();
    private long pairingGeneration=0,savedPairingGeneration=-1;
    private String candidateTicket="",pendingRequestId="",pendingPollToken="";
    private boolean claimInFlight=false;
    private String candidateTransport="";
    private JSONObject previousCredentials,candidateIdentity;private JSONObject candidateDiscovery;
    private IdentityProof.Pin candidatePin;private org.json.JSONArray candidateEndpoints;
    private volatile boolean subscribed=false,pageReady=false;
    private Intent sharedIntent;
    private final ArrayDeque<String> pendingAdb=new ArrayDeque<>();

    @Override public void onCreate(Bundle bundle){
        super.onCreate(bundle);credentials=new Credentials(this);routes=NetworkRoutes.get(this);ConnectionService.channels(this);sharedIntent=getIntent();if(captureAdb(sharedIntent))sharedIntent=null;
        web=new WebView(this);setContentView(web);
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);settings.setAllowContentAccess(true);settings.setAllowFileAccessFromFileURLs(false);settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);settings.setSupportMultipleWindows(false);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.addJavascriptInterface(new PocketBridge(),"Pocket");
        web.setWebViewClient(new WebViewClient(){
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
                Uri u=request.getUrl();
                if(!"https".equals(u.getScheme())||!"appassets.androidplatform.net".equals(u.getHost()))return denied();
                String path=u.getPath();if(path==null)return denied();
                try{
                    if(path.startsWith("/assets/")&&!path.contains("..")&&!path.contains("%")&&request.getMethod().equals("GET")){
                        String asset=path.substring(8);if(asset.isEmpty())asset="index.html";
                        String mime=asset.endsWith(".html")?"text/html":asset.endsWith(".js")?"text/javascript":asset.endsWith(".css")?"text/css":asset.endsWith(".svg")?"image/svg+xml":asset.endsWith(".json")?"application/json":"application/octet-stream";
                        return new WebResourceResponse(mime,"UTF-8",getAssets().open(asset));
                    }
                    if(path.matches("/api/media/[A-Za-z0-9_-]{1,128}")){
                        JSONObject auth=credentials.read();if(auth==null)return denied();
                        ApiClient.Result r=routes.call(auth,path,"GET",null,null,ApiClient.MAX_MEDIA,null);
                        if(r.status()!=200||!safeMime(r.mime()))return denied();return new WebResourceResponse(r.mime(),null,new ByteArrayInputStream(r.bytes()));
                    }
                }catch(Exception ignored){}return denied();
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                Uri u=request.getUrl();if(isBundled(u))return false;
                if(request.isForMainFrame()&&("https".equals(u.getScheme())||"http".equals(u.getScheme())))try{startActivity(new Intent(Intent.ACTION_VIEW,u));}catch(Exception ignored){}
                return true;
            }
            @Override public void onPageFinished(WebView view,String url){pageReady=isBundled(Uri.parse(url));if(pageReady)dispatchShared();}
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onShowFileChooser(WebView view,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(fileCallback!=null)fileCallback.onReceiveValue(null);fileCallback=callback;
                Intent i=new Intent(Intent.ACTION_OPEN_DOCUMENT);i.addCategory(Intent.CATEGORY_OPENABLE);i.setType("image/*");i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,true);
                try{startActivityForResult(i,FILE_REQUEST);}catch(Exception e){fileCallback.onReceiveValue(null);fileCallback=null;}return true;
            }
        });
        web.loadUrl(LOCAL+"/assets/index.html");
    }
    private static boolean isBundled(Uri u){return "https".equals(u.getScheme())&&"appassets.androidplatform.net".equals(u.getHost())&&u.getPath()!=null&&u.getPath().startsWith("/assets/");}
    private static WebResourceResponse denied(){return new WebResourceResponse("text/plain","UTF-8",403,"Blocked",Map.of("Cache-Control","no-store"),new ByteArrayInputStream("Blocked".getBytes(StandardCharsets.UTF_8)));}
    private static boolean safeMime(String mime){return mime!=null&&(mime.split(";")[0].matches("image/(png|jpeg|webp|gif)"));}
    private void callback(String name,Object...args){
        StringBuilder call=new StringBuilder("if(window.").append(name).append("){window.").append(name).append('(');
        for(int i=0;i<args.length;i++){if(i>0)call.append(',');Object a=args[i];call.append(a instanceof Number||a instanceof Boolean?a.toString():JSONObject.quote(String.valueOf(a)));}call.append(");}");
        runOnUiThread(()->{if(pageReady&&web!=null&&!isFinishing())web.evaluateJavascript(call.toString(),null);});
    }
    private void response(String id,int status,String body){callback("pocketResponse",id,status,body);}
    private void error(String id,int status,String code,String message){response(id,status,ApiClient.error(code,message).toString());}
    private void event(JSONObject e){
        final long expected=e.optLong("_connection_epoch",credentials.epoch());
        runOnUiThread(()->{if(credentials.epoch()==expected)callback("pocketEvent",e.toString());});
    }
    private void authenticatedEvent(JSONObject e,JSONObject source){
        if(!credentials.matches(source))return;
        long expected=source.optLong("_epoch",-1);
        if("unauthorized".equals(e.optString("type"))){expected=credentials.invalidateIfMatches(source,()->{rotateApiScope();stream.stop();stopService(new Intent(this,ConnectionService.class));});if(expected<0)return;routes.changed();}
        final long epoch=expected;
        runOnUiThread(()->{if(credentials.epoch()==epoch)callback("pocketEvent",e.toString());});
    }
    private void networkChanged(JSONObject value){
        long next=value.optLong("route_generation",-1);if(next!=streamRouteGeneration){streamRouteGeneration=next;stream.nudge();}
        try{JSONObject event=new JSONObject(value.toString()).put("type","network");event(event);}catch(Exception ignored){}
    }
    private boolean backgroundEnabled(){return getPreferences(MODE_PRIVATE).getBoolean("background",false);}
    private void subscribeNative(){
        JSONObject auth=credentials.read();if(auth==null){event(ApiClient.error("not_paired","请先配对"));return;}
        subscribed=true;routes.attach(this,activityForeground,this::networkChanged);
        if(backgroundEnabled()){
            stream.stop();ConnectionService.listener=this::event;startForegroundService(new Intent(this,ConnectionService.class));
        }else stream.start(routes,auth,e->authenticatedEvent(e,auth));
    }
    private void scan(){runOnUiThread(()->{new IntentIntegrator(this).setDesiredBarcodeFormats(IntentIntegrator.QR_CODE).setBeepEnabled(false).setOrientationLocked(false).setPrompt("扫描电脑端的一次性配对二维码").initiateScan();});}
    private void rotateApiScope(){ApiClient.Scope previous=apiScope;apiScope=new ApiClient.Scope();previous.close();}
    private void cancelPairingLocked(){
        if(savedPairingGeneration==pairingGeneration){
            try{if(previousCredentials==null)credentials.clear();else credentials.restore(previousCredentials);}
            catch(Exception ignored){credentials.clear();}rotateApiScope();if(routes!=null)routes.changed();
        }
        ApiClient.Scope oldPairingScope=pairingScope;pairingScope=new ApiClient.Scope();oldPairingScope.close();
        pairingGeneration++;savedPairingGeneration=-1;previousCredentials=null;candidateOrigin="";candidateTransport="";candidateTicket="";candidateIdentity=null;candidateDiscovery=null;candidatePin=null;candidateEndpoints=null;pendingRequestId="";pendingPollToken="";claimInFlight=false;
    }
    private void startCandidate(String origin,String ticket){startCandidate(origin,ticket,null,null);}
    private void startCandidate(String origin,String ticket,JSONObject identity,org.json.JSONArray endpoints){startCandidate(origin,ticket,identity,endpoints,null);}
    private void startCandidate(String origin,String ticket,JSONObject identity,org.json.JSONArray endpoints,JSONObject discovery){
        synchronized(pairingLock){cancelPairingLocked();previousCredentials=credentials.read();candidateOrigin=origin;candidateTicket=ticket;candidateIdentity=identity;candidateDiscovery=discovery;candidateEndpoints=endpoints;try{candidatePin=NetworkRoutes.parsePin(identity);}catch(Exception e){throw new IllegalArgumentException("服务身份无效");}}
    }
    private String prepare(String payload){
        synchronized(pairingLock){cancelPairingLocked();}
        try{
            JSONObject p=new JSONObject(payload);
            if(!"codex-pocket-pair".equals(p.optString("type"))||p.optInt("version")!=1||p.optString("ticket").length()<16||p.optString("ticket").length()>256)throw new IllegalArgumentException("不是有效的 Codex Pocket 配对码");
            String origin=ApiClient.origin(p.getString("url"));JSONObject identity=p.optJSONObject("server_identity");NetworkRoutes.parsePin(identity);org.json.JSONArray endpoints=identity==null?null:NetworkRoutes.normalizeEndpoints(p.optJSONArray("endpoints"),origin);JSONObject discovery=DiscoveryManifest.config(p.optJSONObject("discovery"),NetworkRoutes.parsePin(identity));if(discovery==null&&endpoints!=null){for(int i=0;i<endpoints.length();i++)endpoints.getJSONObject(i).put("tls","system").put("managed",false).put("source","manual");}if(discovery!=null&&endpoints!=null){for(int i=0;i<endpoints.length();i++){JSONObject e=endpoints.getJSONObject(i);if(e.optBoolean("managed"))DiscoveryManifest.validateEndpoint(e);}}startCandidate(origin,p.getString("ticket"),identity,endpoints,discovery);
            return new JSONObject().put("url",origin).put("version",1).toString();
        }catch(Exception e){return ApiClient.error("invalid_pairing",e.getMessage()==null?"配对内容无效":e.getMessage()).toString();}
    }
    private static String pairTls(JSONObject discovery,org.json.JSONArray endpoints,String origin)throws Exception{if(discovery==null)return null;return DiscoveryManifest.tlsPin(new JSONObject().put("discovery",discovery).put("endpoints",endpoints),origin);}
    private void cancelConfigImport(){synchronized(configLock){if(configPreparedGeneration==configGeneration){synchronized(pairingLock){cancelPairingLocked();}}configPreparedGeneration=-1;configGeneration++;configBusy=false;configScope.close();configScope=new ApiClient.Scope();}}
    private boolean importOrPairBusy(){synchronized(configLock){if(configBusy)return true;}synchronized(pairingLock){return !candidateOrigin.isEmpty()||claimInFlight||!pendingRequestId.isEmpty();}}
    private String previewConfig(String raw){try{if(importOrPairBusy())return ApiClient.error("import_busy","请先完成或取消当前配对/导入").toString();JSONObject config=ConfigLinks.parse(raw);JSONObject grant=credentials.read();boolean same=ConfigLinks.samePins(config,grant);return config.put("already_paired",same).put("requires_pairing",!same).put("different_server",grant!=null&&!same).toString();}catch(Exception e){return ApiClient.error("invalid_config_link","配置链接无效、版本不受支持或身份字段不完整").toString();}}
    private void receiveConfigLink(String raw){try{if(importOrPairBusy()){callback("pocketEvent","{\"type\":\"pairing_error\",\"message\":\"请先完成或取消当前配对/导入\"}");return;}ConfigLinks.parse(raw);callback("pocketConfigLink",raw);}catch(Exception e){callback("pocketEvent","{\"type\":\"pairing_error\",\"message\":\"配置链接无效或版本不受支持\"}");}}
    private void importConfig(String id,String raw){
        if(id==null||id.length()>128)return;if(importOrPairBusy()){error(id,409,"import_busy","请先完成或取消当前配对/导入");return;}final JSONObject config;try{config=ConfigLinks.parse(raw);}catch(Exception e){error(id,400,"invalid_config_link","配置链接无效或版本不受支持");return;}
        final long generation,credentialEpoch;final JSONObject prior;final ApiClient.Scope scope;synchronized(configLock){if(configBusy){error(id,409,"import_busy","配置导入正在进行");return;}synchronized(pairingLock){if(!candidateOrigin.isEmpty()||claimInFlight||!pendingRequestId.isEmpty()){error(id,409,"import_busy","请先完成或取消当前配对");return;}}configGeneration++;generation=configGeneration;configBusy=true;configScope.close();scope=new ApiClient.Scope();configScope=scope;Credentials.Snapshot snapshot=credentials.snapshot();prior=snapshot.auth();credentialEpoch=snapshot.epoch();}
        try{configIo.execute(()->{try{String origin=ConfigImport.choose(config,scope);JSONObject result;if(ConfigLinks.samePins(config,prior)){JSONObject state=routes.importFromSeed(prior,origin,config.getJSONObject("discovery").getString("tls_spki"),scope,configLock,()->generation==configGeneration&&credentials.epoch()==credentialEpoch);result=new JSONObject().put("already_paired",true).put("network_status",state);}else{JSONObject pairing=ConfigImport.pairing(config,origin,scope);synchronized(configLock){if(generation!=configGeneration||scope.isClosed()||credentialEpoch!=credentials.epoch())throw new IOException("配置导入已取消");String prepared=prepare(pairing.toString());if(new JSONObject(prepared).has("error"))throw new IllegalArgumentException("配对配置无效");configPreparedGeneration=generation;result=new JSONObject().put("already_paired",false).put("pairing",pairing);}}
            runOnUiThread(()->{if(generation==configGeneration&&credentialEpoch==credentials.epoch())response(id,200,result.toString());else error(id,409,"import_cancelled","配置导入已取消或连接已切换");});
        }catch(Exception e){boolean current=generation==configGeneration&&credentialEpoch==credentials.epoch();String code=current&&"config_revoked".equals(e.getMessage())?"config_revoked":current?"config_import_failed":"import_cancelled";error(id,current?503:409,code,code.equals("config_revoked")?"此配置链接已被电脑撤销，请获取新链接":current?"未能确认配置。请求结果未知时请勿重复导入；请检查连接和电脑配置":"配置导入已取消或连接已切换");}finally{synchronized(configLock){if(generation==configGeneration)configBusy=false;}}});}catch(RejectedExecutionException e){synchronized(configLock){if(generation==configGeneration)configBusy=false;}error(id,429,"import_busy","配置导入正在结束，请稍后重试");}
    }
    private final class PocketBridge {
        @JavascriptInterface public String previewConfigLink(String raw){return previewConfig(raw);}
        @JavascriptInterface public void importConfigLink(String id,String raw){importConfig(id,raw);}
        @JavascriptInterface public void cancelConfigImport(){MainActivity.this.cancelConfigImport();}
        @JavascriptInterface public String getConnection(){
            try{JSONObject auth=credentials.read();return new JSONObject().put("paired",auth!=null).put("url",auth==null?"":auth.optString("url")).put("deviceName",auth==null?Build.MODEL:auth.optString("deviceName",Build.MODEL)).put("backgroundEnabled",backgroundEnabled()).put("notificationsGranted",Build.VERSION.SDK_INT<33||checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)==PackageManager.PERMISSION_GRANTED).put("unofficial",true).put("serverId",auth==null?"":auth.optJSONObject("server_identity")==null?"":auth.optJSONObject("server_identity").optString("id")).put("active_url",routes.status().optString("active_url")).toString();}catch(Exception e){return "{\"paired\":false}";}
        }
        @JavascriptInterface public String getNetworkStatus(){return routes.status().toString();}
        @JavascriptInterface public void configureNetworks(String id,String config){networkControl(id,config,false);}
        @JavascriptInterface public void probeNetworks(String id){networkControl(id,null,true);}
        private void networkControl(String id,String config,boolean probe){final JSONObject source=credentials.read();try{routeControl.execute(()->{try{if(source==null||!credentials.matches(source)){error(id,409,"stale_connection","请先配对或连接已切换");return;}JSONObject state=probe?routes.probeNow(source):routes.configure(new JSONObject(config),source);if(!credentials.matches(source)){error(id,409,"stale_connection","连接已经切换");return;}response(id,200,state.toString());}catch(IllegalArgumentException|JSONException e){error(id,400,"invalid_routes",e.getMessage());}catch(Exception e){if(source!=null&&!credentials.matches(source))error(id,409,"stale_connection","连接已经切换");else error(id,503,"routes_unavailable","线路检测未完成，请检查地址和电脑登记");}});}catch(RejectedExecutionException e){error(id,429,"routes_busy","线路检测正在进行");}}
        @JavascriptInterface public String preparePairing(String payload){cancelConfigImport();return prepare(payload);}
        @JavascriptInterface public String setServer(String url){cancelConfigImport();try{String origin=ApiClient.origin(url);startCandidate(origin,"");return origin;}catch(Exception e){return ApiClient.error("invalid_server",e.getMessage()).toString();}}
        @JavascriptInterface public void scanPairing(){cancelConfigImport();synchronized(pairingLock){cancelPairingLocked();}scan();}
        @JavascriptInterface public void cancelPairing(){cancelConfigImport();synchronized(pairingLock){cancelPairingLocked();}}
        @JavascriptInterface public void finishPairing(){synchronized(pairingLock){if(savedPairingGeneration==pairingGeneration){savedPairingGeneration=-1;previousCredentials=null;pairingGeneration++;ApiClient.Scope oldPairingScope=pairingScope;pairingScope=new ApiClient.Scope();oldPairingScope.close();candidateOrigin="";candidateTransport="";candidateTicket="";candidateIdentity=null;candidateDiscovery=null;candidatePin=null;candidateEndpoints=null;pendingRequestId="";pendingPollToken="";claimInFlight=false;}}}
        @JavascriptInterface public void request(String id,String method,String path,String body){
            if(id==null||id.length()>128)return;
            final JSONObject queuedAuth;final ApiClient.Scope queuedScope,queuedPairScope;final long queuedGeneration;final String queuedOrigin,queuedTransport;final IdentityProof.Pin queuedPin;final JSONObject queuedIdentity,queuedDiscovery;final org.json.JSONArray queuedEndpoints;
            synchronized(pairingLock){queuedAuth=credentials.read();queuedScope=apiScope;queuedPairScope=pairingScope;queuedGeneration=pairingGeneration;queuedOrigin=candidateOrigin;queuedTransport=candidateTransport;queuedPin=candidatePin;queuedIdentity=candidateIdentity;queuedDiscovery=candidateDiscovery;queuedEndpoints=candidateEndpoints;}
            final boolean queuedPair=path!=null&&path.startsWith("/pair/");
            (queuedPair?pairingIo:io).execute(()->{
                try{
                    if(body!=null&&body.length()>128*1024)throw new IllegalArgumentException("请求过大");
                    if(!ApiClient.allowed(method,path))throw new IllegalArgumentException("该接口未开放");
                    boolean pair=queuedPair;JSONObject auth=queuedAuth;
                    JSONObject input=body==null||body.isEmpty()?null:new JSONObject(body);
                    String base;long pairGen=-1;String requestBinding="",pollBinding="";
                    if(pair){
                        synchronized(pairingLock){
                            base=queuedOrigin;pairGen=queuedGeneration;
                            if(pairGen!=pairingGeneration||!base.equals(candidateOrigin)){error(id,409,"pairing_cancelled","配对已经切换");return;}
                            if(base.isEmpty()){error(id,409,"pairing_cancelled","配对已取消或尚未准备");return;}
                            if(path.equals("/pair/claim")){
                                if(input==null||claimInFlight||!pendingRequestId.isEmpty()||(!candidateTicket.isEmpty()&&!candidateTicket.equals(input.optString("ticket")))){error(id,409,"pairing_conflict","配对请求已存在或内容不匹配");return;}
                                candidateDeviceName=input.optString("device_name",Build.MODEL);claimInFlight=true;
                            }else{
                                if(input==null||pendingRequestId.isEmpty()||!pendingRequestId.equals(input.optString("request_id"))||!pendingPollToken.equals(input.optString("poll_token"))){error(id,409,"pairing_conflict","配对请求已失效或不匹配");return;}
                                requestBinding=pendingRequestId;pollBinding=pendingPollToken;
                            }
                        }
                    }else{if(auth!=null&&!credentials.matches(auth)){error(id,409,"stale_connection","连接已经切换，已忽略旧操作");return;}base=auth==null?"":auth.optString("url");if(base.isEmpty()){error(id,401,"not_paired","请先扫描服务端配对码");return;}}
                    ApiClient.Result r;
                    try{String transport=base;if(pair){transport=queuedTransport;if(transport.isEmpty())transport=PairingRoutes.choose(base,queuedPin,queuedDiscovery,queuedEndpoints,queuedPairScope);synchronized(pairingLock){if(pairGen!=pairingGeneration||!base.equals(candidateOrigin))throw new IOException("配对已切换");candidateTransport=transport;}}byte[] payload=input==null?null:input.toString().getBytes(StandardCharsets.UTF_8);r=pair?ApiClient.call(transport,path,null,method,payload,"application/json",4*1024*1024,queuedPairScope,null,pairTls(queuedDiscovery,queuedEndpoints,transport)):routes.call(auth,path,method,payload,"application/json",4*1024*1024,queuedScope);}
                    catch(Exception e){if(pair){synchronized(pairingLock){if(pairGen==pairingGeneration){if(path.equals("/pair/claim"))claimInFlight=false;else candidateTransport="";}}}throw e;}
                    String result=r.text();
                    if(pair){synchronized(pairingLock){
                        if(pairGen!=pairingGeneration||!base.equals(candidateOrigin)){error(id,409,"pairing_cancelled","配对已取消，已忽略迟到结果");return;}
                        if(path.equals("/pair/claim")){
                            claimInFlight=false;
                            if(r.status()==200){JSONObject p=new JSONObject(result);String requestId=p.optString("request_id"),pollToken=p.optString("poll_token");if(requestId.isEmpty()||pollToken.length()<16)throw new IOException("服务端配对响应无效");pendingRequestId=requestId;pendingPollToken=pollToken;}
                        }else{
                            if(!requestBinding.equals(pendingRequestId)||!pollBinding.equals(pendingPollToken)){error(id,409,"pairing_conflict","配对请求已失效");return;}
                            if(r.status()==200){JSONObject p=new JSONObject(result);String token=p.optString("token");p.remove("token");result=p.toString();
                                if("approved".equals(p.optString("status"))){
                                    if(token.length()<16)throw new IOException("服务端凭据无效");
                                    if(queuedPin!=null){IdentityProof.Pin approved=NetworkRoutes.parsePin(p.optJSONObject("server_identity"));if(approved==null||!approved.id().equals(queuedPin.id())||!approved.key().equals(queuedPin.key()))throw new java.security.GeneralSecurityException("批准的服务身份与二维码不一致");}
                                    credentials.save(base,token,p.optString("device_id"),candidateDeviceName,queuedIdentity,queuedEndpoints,queuedDiscovery);rotateApiScope();routes.changed();savedPairingGeneration=pairGen;
                                }
                            }
                        }
                    }}
                    if(!pair&&!credentials.matches(auth)){error(id,409,"stale_connection","连接已经切换，已忽略旧连接的结果");return;}
                    if(!pair&&r.status()==401&&!routes.current(r,auth)){error(id,409,"stale_route","已忽略旧线路的认证结果");return;}
                    if(!pair&&NetworkRoutes.revoked(r)){long changed=credentials.invalidateIfMatches(auth,()->{rotateApiScope();stream.stop();stopService(new Intent(MainActivity.this,ConnectionService.class));});if(changed<0){error(id,409,"stale_connection","连接已经切换");return;}routes.changed();final long expectedEpoch=changed;runOnUiThread(()->{if(credentials.epoch()==expectedEpoch)callback("pocketEvent","{\"type\":\"unauthorized\"}");});}
                    response(id,r.status(),result);
                }catch(IllegalArgumentException e){error(id,400,"invalid_request",e.getMessage());}catch(Exception e){
                    boolean stalePair;synchronized(pairingLock){stalePair=queuedPair&&(queuedGeneration!=pairingGeneration||!queuedOrigin.equals(candidateOrigin));}
                    if(stalePair)error(id,409,"pairing_cancelled","配对已经切换，已忽略旧结果");else if(!queuedPair&&queuedAuth!=null&&!credentials.matches(queuedAuth))error(id,409,"stale_connection","连接已经切换，已忽略旧操作");else error(id,503,"network_unavailable","未收到服务端确认。请检查连接；发送结果未知时不要重复发送。");
                }
            });
        }
        @JavascriptInterface public void upload(String id,String mime,String base64){
            final JSONObject queuedAuth=credentials.read();final ApiClient.Scope queuedScope=apiScope;
            io.execute(()->{try{
                if(!safeMime(mime)||base64.length()>21*1024*1024)throw new IllegalArgumentException("图片格式或大小不支持");
                byte[] data=Base64.decode(base64,Base64.DEFAULT);if(data.length>ApiClient.MAX_MEDIA)throw new IllegalArgumentException("图片超过 15MB");
                JSONObject auth=queuedAuth;if(auth==null){error(id,401,"not_paired","请先配对");return;}if(!credentials.matches(auth)){error(id,409,"stale_connection","连接已经切换");return;}
                ApiClient.Result r=routes.call(auth,"/api/media","POST",data,mime,1024*1024,queuedScope);if(!credentials.matches(auth)){error(id,409,"stale_connection","连接已经切换");return;}response(id,r.status(),r.text());
            }catch(IllegalArgumentException e){error(id,400,"invalid_image",e.getMessage());}catch(Exception e){if(queuedAuth!=null&&!credentials.matches(queuedAuth))error(id,409,"stale_connection","连接已经切换");else error(id,503,"upload_failed","图片上传未完成，请检查连接");}});
        }
        @JavascriptInterface public void media(String id,String mediaId){
            final JSONObject queuedAuth=credentials.read();final ApiClient.Scope queuedScope=apiScope;
            io.execute(()->{try{
                JSONObject auth=queuedAuth;if(auth==null){error(id,401,"not_paired","请先配对");return;}if(!credentials.matches(auth)){error(id,409,"stale_connection","连接已经切换");return;}
                ApiClient.Result r=routes.call(auth,"/api/media/"+mediaId,"GET",null,null,ApiClient.MAX_MEDIA,queuedScope);
                if(!credentials.matches(auth)){error(id,409,"stale_connection","连接已经切换");return;}if(r.status()!=200){response(id,r.status(),r.text());return;}if(!safeMime(r.mime()))throw new IllegalArgumentException("图片格式不支持");
                response(id,200,new JSONObject().put("data_url","data:"+r.mime().split(";")[0]+";base64,"+Base64.encodeToString(r.bytes(),Base64.NO_WRAP)).toString());
            }catch(Exception e){if(queuedAuth!=null&&!credentials.matches(queuedAuth))error(id,409,"stale_connection","连接已经切换");else error(id,503,"media_failed","图片读取失败");}});
        }
        @JavascriptInterface public void subscribe(){runOnUiThread(()->subscribeNative());}
        @JavascriptInterface public void unsubscribe(){subscribed=false;stream.stop();stopService(new Intent(MainActivity.this,ConnectionService.class));}
        @JavascriptInterface public void disconnect(){
            cancelConfigImport();synchronized(pairingLock){cancelPairingLocked();credentials.clear();rotateApiScope();routes.reset();routes.detach(MainActivity.this);}subscribed=false;stream.stop();stopService(new Intent(MainActivity.this,ConnectionService.class));
            getPreferences(MODE_PRIVATE).edit().putBoolean("background",false).apply();
            // Explicit disconnect is initiated by the UI; echoing unauthorized would recurse.

        }
        @JavascriptInterface public void setBackgroundEnabled(boolean enabled){runOnUiThread(()->{
            if(enabled&&Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},NOTIFY_REQUEST);return;}
            getPreferences(MODE_PRIVATE).edit().putBoolean("background",enabled).apply();if(!enabled)stopService(new Intent(MainActivity.this,ConnectionService.class));if(subscribed)subscribeNative();callback("pocketEvent","{\"type\":\"settings\"}");
        });}
        @JavascriptInterface public void showNotificationSettings(){runOnUiThread(()->startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE,getPackageName())));}
    }
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] grantResults){super.onRequestPermissionsResult(code,permissions,grantResults);if(code==NOTIFY_REQUEST&&grantResults.length>0&&grantResults[0]==PackageManager.PERMISSION_GRANTED){getPreferences(MODE_PRIVATE).edit().putBoolean("background",true).apply();if(subscribed)subscribeNative();callback("pocketEvent","{\"type\":\"settings\"}");}}
    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data){
        IntentResult scan=IntentIntegrator.parseActivityResult(requestCode,resultCode,data);
        if(scan!=null){if(scan.getContents()!=null){if(scan.getContents().startsWith("codexpocket:")){receiveConfigLink(scan.getContents());return;}String value=prepare(scan.getContents());try{if(new JSONObject(value).has("error"))callback("pocketEvent",new JSONObject().put("type","pairing_error").put("message","配对码无效或不受支持").toString());else callback("pocketPairing",scan.getContents());}catch(Exception ignored){}}return;}
        if(requestCode==FILE_REQUEST){
            if(fileCallback!=null){ArrayList<Uri> items=new ArrayList<>();if(resultCode==RESULT_OK&&data!=null){if(data.getClipData()!=null){for(int i=0;i<Math.min(4,data.getClipData().getItemCount());i++)items.add(data.getClipData().getItemAt(i).getUri());}else if(data.getData()!=null)items.add(data.getData());}fileCallback.onReceiveValue(items.isEmpty()?null:items.toArray(new Uri[0]));fileCallback=null;}return;
        }
        super.onActivityResult(requestCode,resultCode,data);
    }
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);sharedIntent=intent;if(captureAdb(intent))sharedIntent=null;if(pageReady)dispatchShared();}
    private boolean captureAdb(Intent intent){if(intent==null||!AdbCommandReceiver.IMPORT_ACTION.equals(intent.getAction()))return false;String id=intent.getStringExtra("request_id");if(AdbFiles.requestId(id)&&AdbImports.pending(this,id)&&pendingAdb.size()<4&&!pendingAdb.contains(id))pendingAdb.add(id);return true;}
    private void dispatchAdb(){while(!pendingAdb.isEmpty()){String id=pendingAdb.remove();io.execute(()->{try{AdbImports.Payload payload=AdbImports.take(this,id);if(payload==null)return;boolean pairing=payload.command().equals("import-pairing");String argument;if(pairing){cancelConfigImport();argument=new String(payload.bytes(),StandardCharsets.UTF_8);if(new JSONObject(prepare(argument)).has("error")){AdbCommandReceiver.finishImport(this,id,false,"invalid_pairing");return;}}else argument="data:"+payload.mime()+";base64,"+Base64.encodeToString(payload.bytes(),Base64.NO_WRAP);
                final String value=argument;runOnUiThread(()->{if(web==null||!pageReady){AdbCommandReceiver.finishImport(this,id,false,"ui_unavailable");return;}String script=pairing?"(function(){if(!window.pocketPairing)return false;window.pocketPairing("+JSONObject.quote(value)+");return !!document.querySelector('[data-action=\"claim-pair\"]');})()":"(function(){if(!window.pocketSharedImage)return false;var before=document.querySelectorAll('#attachments .attachment').length;if(before>=4)return false;window.pocketSharedImage("+JSONObject.quote(value)+");return document.querySelectorAll('#attachments .attachment').length===before+1;})()";web.evaluateJavascript(script,result->AdbCommandReceiver.finishImport(this,id,"true".equals(result),"true".equals(result)?pairing?"pairing_preview":"image_draft":"ui_rejected"));});
            }catch(Exception ignored){AdbCommandReceiver.finishImport(this,id,false,"import_failed");}});}}
    private void dispatchShared(){
        dispatchAdb();Intent intent=sharedIntent;sharedIntent=null;if(intent==null)return;if(Intent.ACTION_VIEW.equals(intent.getAction())){receiveConfigLink(intent.getDataString());return;}
        ArrayList<Uri> uris=new ArrayList<>();if(Intent.ACTION_SEND.equals(intent.getAction())){Uri u=intent.getParcelableExtra(Intent.EXTRA_STREAM);if(u!=null)uris.add(u);}else if(Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())){ArrayList<Uri> incoming=intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);if(incoming!=null)uris.addAll(incoming);}
        for(Uri uri:uris.subList(0,Math.min(4,uris.size())))io.execute(()->{try{String mime=getContentResolver().getType(uri);if(!safeMime(mime))return;byte[] data=ApiClient.read(getContentResolver().openInputStream(uri),ApiClient.MAX_MEDIA);callback("pocketSharedImage","data:"+mime+";base64,"+Base64.encodeToString(data,Base64.NO_WRAP));}catch(Exception ignored){callback("pocketEvent","{\"type\":\"share_error\",\"message\":\"图片无法读取或超过15MB\"}");}});
    }
    @Override protected void onResume(){super.onResume();activityForeground=true;ConnectionService.listener=this::event;routes.attach(this,true,this::networkChanged);if(subscribed&&!backgroundEnabled())subscribeNative();
        if(Build.VERSION.SDK_INT>=33&&backgroundEnabled()&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){getPreferences(MODE_PRIVATE).edit().putBoolean("background",false).apply();stopService(new Intent(this,ConnectionService.class));if(subscribed)subscribeNative();}
        if(pageReady)callback("pocketEvent","{\"type\":\"settings\"}");
    }
    @Override protected void onPause(){activityForeground=false;routes.detach(this);if(!backgroundEnabled())stream.stop();super.onPause();}
    @Override protected void onDestroy(){cancelConfigImport();configIo.shutdownNow();pageReady=false;routes.detach(this);synchronized(pairingLock){cancelPairingLocked();}stream.stop();ConnectionService.listener=null;if(fileCallback!=null)fileCallback.onReceiveValue(null);apiScope.close();if(web!=null){web.removeJavascriptInterface("Pocket");web.destroy();web=null;}io.shutdownNow();pairingIo.shutdownNow();routeControl.shutdownNow();super.onDestroy();}
    @Override public void onBackPressed(){if(web==null||!pageReady){finish();return;}web.evaluateJavascript("(function(){return window.pocketBack ? window.pocketBack() : false;})()",value->{if(!"true".equals(value))finish();});}
}
