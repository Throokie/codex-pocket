package io.github.codexpocket.app;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
public final class CoalescingTaskTest {
 public static void main(String[] args)throws Exception{
  AtomicInteger submitted=new AtomicInteger(),executed=new AtomicInteger();CountDownLatch entered=new CountDownLatch(1),release=new CountDownLatch(1),second=new CountDownLatch(1);
  ExecutorService delegate=Executors.newSingleThreadExecutor();Executor counted=r->{submitted.incrementAndGet();delegate.execute(r);};
  CoalescingTask task=new CoalescingTask(counted,()->{int n=executed.incrementAndGet();if(n==1){entered.countDown();try{release.await();}catch(InterruptedException e){Thread.currentThread().interrupt();}}else second.countDown();});
  task.trigger();if(!entered.await(2,TimeUnit.SECONDS))throw new AssertionError("first worker missing");for(int i=0;i<10000;i++)task.trigger();
  if(submitted.get()!=1||executed.get()!=1)throw new AssertionError("events accumulated jobs");release.countDown();if(!second.await(2,TimeUnit.SECONDS))throw new AssertionError("dirty followup missing");
  task.close();for(int i=0;i<100;i++)task.trigger();delegate.shutdown();if(!delegate.awaitTermination(2,TimeUnit.SECONDS))throw new AssertionError("worker did not close");
  if(executed.get()!=2||submitted.get()!=1)throw new AssertionError("unexpected replay");System.out.println("PASS: 10000 events coalesce to 1 worker, 2 reads; closed task ignores triggers");
 }
}
