package io.github.codexpocket.app;
import java.io.IOException;
import java.util.*;
import java.util.concurrent.*;
final class VerifiedCandidates {
 interface Verify{void verify(String origin)throws Exception;}
 private static final ThreadPoolExecutor workers=new ThreadPoolExecutor(3,3,30,TimeUnit.SECONDS,new ArrayBlockingQueue<>(6),r->{Thread t=new Thread(r,"pocket-pair-proof");t.setDaemon(true);return t;});
 static String choose(Collection<String> origins,Verify verify,long timeoutMs)throws Exception{
  ExecutorCompletionService<String> completed=new ExecutorCompletionService<>(workers);List<Future<String>> jobs=new ArrayList<>();for(String origin:origins){try{jobs.add(completed.submit(()->{verify.verify(origin);return origin;}));}catch(RejectedExecutionException ignored){}}
  long deadline=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(timeoutMs);try{for(int i=0;i<jobs.size();i++){long left=deadline-System.nanoTime();if(left<=0)break;Future<String> result=completed.poll(left,TimeUnit.NANOSECONDS);if(result==null)break;try{return result.get();}catch(ExecutionException ignored){}}throw new IOException("二维码中的服务地址暂不可达");}finally{for(Future<String> job:jobs)job.cancel(true);workers.purge();}
 }
}
