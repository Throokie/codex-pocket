package io.github.codexpocket.app;
public final class StreamFailurePolicyTest {
 public static void main(String[] args){
  int tests=0;
  for(int status:new int[]{401,403,429,500,502,503}){if(StreamFailurePolicy.shouldProbe(status,true,true))throw new AssertionError("HTTP error probes: "+status);tests++;}
  for(int status:new int[]{-1,200}){if(!StreamFailurePolicy.shouldProbe(status,true,true))throw new AssertionError("transport failure needs verification");if(StreamFailurePolicy.shouldProbe(status,false,true))throw new AssertionError("nudge probes");if(StreamFailurePolicy.shouldProbe(status,true,false))throw new AssertionError("stop probes");tests+=3;}
  long delay=1000;for(long expected:new long[]{2000,4000,8000,15000,15000,15000}){delay=StreamFailurePolicy.nextBackoff(delay);if(delay!=expected)throw new AssertionError("unbounded reconnect delay");tests++;}
  System.out.println("PASS TOTAL "+tests);
 }
}
