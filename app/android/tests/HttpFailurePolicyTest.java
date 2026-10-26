package io.github.codexpocket.app;
public final class HttpFailurePolicyTest {
 public static void main(String[] args){int tests=0;
  for(int status:new int[]{200,400,403,404,409,429,500,502,503,504}){if(HttpFailurePolicy.gatewayAuthentication(status,false))throw new AssertionError("HTTP response changed route: "+status);tests++;}
  if(!HttpFailurePolicy.gatewayAuthentication(401,false))throw new AssertionError("gateway auth was missed");tests++;
  if(HttpFailurePolicy.gatewayAuthentication(401,true))throw new AssertionError("revocation treated as gateway failure");tests++;
  System.out.println("PASS TOTAL "+tests);
 }
}
