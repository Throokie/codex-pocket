package io.github.codexpocket.app;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
public final class AdbFilesTest {
 interface Work{void run()throws Exception;}static int tests;static void check(boolean ok,String name){if(!ok)throw new AssertionError(name);tests++;System.out.println("PASS "+name);}static void reject(Work w,String name)throws Exception{try{w.run();}catch(Exception expected){check(true,name);return;}throw new AssertionError(name);}
 public static void main(String[] args)throws Exception{Path base=Files.createTempDirectory(Path.of(args[0]),"adb-file-test-");AdbFiles files=new AdbFiles(base.toFile());check(Files.isDirectory(files.inbox)&&Files.isDirectory(files.outbox)&&Files.isDirectory(files.media),"init fixed directories");
  for(String name:List.of("../pair.json","/pair.json","a/b.json","a\\b.json",".hidden","x..json","a b.json"))check(!AdbFiles.basename(name),"reject basename "+name);check(AdbFiles.basename("pairing-123.json"),"ASCII basename accepted");check(!AdbFiles.requestId("../../x")&&AdbFiles.requestId("request_123"),"strict request ID");
  Files.writeString(files.inbox.resolve("pair.json"),"private-ticket");reject(()->files.read("pair.json",3),"oversize read rejected");check(Files.exists(files.inbox.resolve("pair.json")),"validation failure preserves source");
  Path outside=base.resolve("outside.json");Files.writeString(outside,"outside");Files.createSymbolicLink(files.inbox.resolve("link.json"),outside);reject(()->files.read("link.json",100),"symlink input rejected");reject(()->files.read("../outside.json",100),"path escape rejected");
  Path privateDir=Files.createDirectory(base.resolve("private-stage"));reject(()->files.stageAndConsume("pair.json",privateDir,"request_a","private-ticket".getBytes(StandardCharsets.UTF_8),()->{throw new java.io.IOException("history failure");}),"history commit failure rejected");check(Files.exists(files.inbox.resolve("pair.json")),"history failure preserves input");
  Path badStage=base.resolve("not-directory");Files.writeString(badStage,"x");reject(()->files.stageAndConsume("pair.json",badStage,"request_b",new byte[]{1},()->{}),"stage failure rejected");check(Files.exists(files.inbox.resolve("pair.json")),"stage failure preserves input");
  files.stageAndConsume("pair.json",privateDir,"request_c","private-ticket".getBytes(StandardCharsets.UTF_8),()->{});check(!Files.exists(files.inbox.resolve("pair.json"))&&Files.exists(privateDir.resolve("request_c.bin")),"only committed private queue consumes input");
  check(AdbFiles.mime("x.png",new byte[]{(byte)137,80,78,71,13,10,26,10}).equals("image/png"),"PNG signature");reject(()->AdbFiles.mime("x.jpg",new byte[]{(byte)137,80,78,71,13,10,26,10}),"extension signature mismatch");reject(()->AdbFiles.mime("x.png","fake".getBytes(StandardCharsets.UTF_8)),"fake image rejected");
  files.result("request_123","{\"status\":\"ok\"}");files.result("request_123","{\"status\":\"prepared\"}");check(Files.readString(files.outbox.resolve("result-request_123.json")).equals("{\"status\":\"prepared\"}"),"atomic result replacement");
  check(!AdbFiles.hash("import-pairing",new byte[]{1}).equals(AdbFiles.hash("import-image",new byte[]{1})),"idempotency hash binds command");System.out.println("PASS TOTAL "+tests);
 }
}
