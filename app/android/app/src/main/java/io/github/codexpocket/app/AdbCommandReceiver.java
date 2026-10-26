package io.github.codexpocket.app;
import android.content.*;
import java.util.concurrent.*;
/** Shell-only file command entry; manifest enforces android.permission.DUMP. */
public final class AdbCommandReceiver extends BroadcastReceiver {
 public static final String ACTION="io.github.codexpocket.app.ADB_COMMAND";
 static final String IMPORT_ACTION="io.github.codexpocket.app.INTERNAL_IMPORT";
 private static final ThreadPoolExecutor worker=new ThreadPoolExecutor(1,1,0,TimeUnit.MILLISECONDS,new ArrayBlockingQueue<>(8));
 static void finishImport(Context context,String id,boolean success,String code){try{worker.execute(()->AdbImports.finish(context,id,success,code));}catch(RejectedExecutionException ignored){}}
 @Override public void onReceive(Context context,Intent intent){if(!ACTION.equals(intent.getAction()))return;String id=intent.getStringExtra("request_id"),command=intent.getStringExtra("command"),file=intent.getStringExtra("file");if(!AdbFiles.requestId(id)){setResultCode(400);setResultData("invalid_request_id");return;}PendingResult pending=goAsync();try{worker.execute(()->{try{AdbImports.command(context.getApplicationContext(),id,command,file);pending.setResultCode(200);pending.setResultData("accepted");}catch(Exception ignored){pending.setResultCode(500);pending.setResultData("command_failed");}finally{pending.finish();}});}catch(RejectedExecutionException e){pending.setResultCode(429);pending.setResultData("busy");pending.finish();}}
}
