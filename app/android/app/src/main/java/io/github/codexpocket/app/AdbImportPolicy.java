package io.github.codexpocket.app;
/** Small deterministic parts of the durable import queue. */
final class AdbImportPolicy {
 static boolean currentId(String id,long epoch){return AdbFiles.requestId(id)&&epoch>=0&&id.startsWith("e"+epoch+"-");}
 static boolean sameContent(String saved,String incoming){return saved!=null&&!saved.isEmpty()&&saved.equals(incoming);}
 static String recovery(String status,long age,boolean sameProcess){if(!status.equals("queued")&&!status.equals("processing"))return "";if(age>120000)return "expired";if(status.equals("processing")&&!sameProcess)return "delivery_unknown";return "";}
}
