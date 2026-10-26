package io.github.codexpocket.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** No credential crosses the JavaScript boundary. Android backup is disabled. */
final class Credentials {
    private static final Object LOCK=new Object();
    private static long epoch=0;
    private static final String ALIAS="codex-pocket-device-v1";
    private final SharedPreferences prefs;
    Credentials(Context c){prefs=c.getSharedPreferences("secure_device",Context.MODE_PRIVATE);}
    private SecretKey key() throws Exception {
        KeyStore ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);
        if(ks.containsAlias(ALIAS))return (SecretKey)ks.getKey(ALIAS,null);
        KeyGenerator gen=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
        gen.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return gen.generateKey();
    }
    void save(String url,String token,String deviceId,String name)throws Exception{save(url,token,deviceId,name,null,null);}
    void save(String url,String token,String deviceId,String name,JSONObject identity,org.json.JSONArray endpoints)throws Exception{save(url,token,deviceId,name,identity,endpoints,null);}
    void save(String url,String token,String deviceId,String name,JSONObject identity,org.json.JSONArray endpoints,JSONObject discovery)throws Exception{
        JSONObject value=new JSONObject().put("url",url).put("token",token).put("deviceId",deviceId).put("deviceName",name);
        if(discovery!=null)value.put("discovery",discovery).put("auto_sync",true).put("discovery_revision",discovery.optLong("revision",0));
        if(identity!=null)value.put("server_identity",identity);if(endpoints!=null)value.put("endpoints",endpoints);write(value,true);
    }
    void restore(JSONObject value)throws Exception{write(new JSONObject(value.toString()),true);}
    private void write(JSONObject value,boolean advance)throws Exception{synchronized(LOCK){
        value.remove("_epoch");Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());
        String iv=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP),encrypted=Base64.encodeToString(cipher.doFinal(value.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);
        if(!prefs.edit().putString("iv",iv).putString("ciphertext",encrypted).commit())throw new IllegalStateException("无法保存设备凭据");if(advance)epoch++;
    }}
    boolean networks(JSONObject expected,org.json.JSONArray endpoints,String mode,String manual)throws Exception{synchronized(LOCK){if(!matches(expected))return false;JSONObject value=read();value.put("endpoints",endpoints).put("network_mode",mode).put("manual_url",manual).put("network_configured",true);write(value,false);return true;}}
    boolean discovery(JSONObject expected,org.json.JSONArray endpoints,long revision,String hash)throws Exception{synchronized(LOCK){if(!matches(expected))return false;JSONObject value=read();DiscoveryProof.revision(value.optLong("discovery_revision",-1),value.optString("discovery_hash"),revision,hash);value.put("endpoints",endpoints).put("network_configured",true).put("discovery_revision",revision).put("discovery_hash",hash).put("last_sync",System.currentTimeMillis());write(value,false);return true;}}
    boolean autoSync(JSONObject expected,boolean enabled)throws Exception{synchronized(LOCK){if(!matches(expected))return false;JSONObject value=read();if(enabled&&value.optJSONObject("discovery")==null)return false;value.put("auto_sync",enabled);write(value,false);return true;}}
    JSONObject read(){synchronized(LOCK){
        try {
            String encrypted=prefs.getString("ciphertext",null);if(encrypted==null)return null;
            Cipher c=Cipher.getInstance("AES/GCM/NoPadding");
            c.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),Base64.NO_WRAP)));
            return new JSONObject(new String(c.doFinal(Base64.decode(encrypted,Base64.NO_WRAP)),StandardCharsets.UTF_8)).put("_epoch",epoch);
        }catch(Exception e){return null;}
    }}
    void clear(){synchronized(LOCK){prefs.edit().clear().commit();epoch++;}}
    record Snapshot(JSONObject auth,long epoch){}
    Snapshot snapshot(){synchronized(LOCK){return new Snapshot(read(),epoch);}}
    long epoch(){synchronized(LOCK){return epoch;}}
    boolean matches(JSONObject expected){synchronized(LOCK){JSONObject current=read();return expected!=null&&current!=null&&expected.optLong("_epoch",-1)==epoch&&expected.optString("url").equals(current.optString("url"))&&expected.optString("token").equals(current.optString("token"));}}
    long invalidateIfMatches(JSONObject expected){return invalidateIfMatches(expected,null);}
    long invalidateIfMatches(JSONObject expected,Runnable action){synchronized(LOCK){if(!matches(expected))return -1;clear();if(action!=null)action.run();return epoch;}}
    boolean runIfMatches(JSONObject expected,Runnable action){synchronized(LOCK){if(!matches(expected))return false;action.run();return true;}}
}
