package io.github.codexpocket.app;
import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
/** Fixed ASCII names and no-follow file reads. No app secrets are written here. */
final class AdbFiles {
 static final int PAIR_LIMIT=64*1024,IMAGE_LIMIT=12*1024*1024;
 final Path root,inbox,outbox,media;
 AdbFiles(File external)throws IOException{if(external==null)throw new IOException("storage_unavailable");Path base=external.toPath();root=directory(base.resolve("CodexPocket"));inbox=directory(root.resolve("inbox"));outbox=directory(root.resolve("outbox"));media=directory(root.resolve("media"));}
 private static Path directory(Path path)throws IOException{if(Files.isSymbolicLink(path))throw new IOException("symlink_rejected");Files.createDirectories(path);if(!Files.isDirectory(path,LinkOption.NOFOLLOW_LINKS))throw new IOException("invalid_directory");return path;}
 static boolean requestId(String id){return id!=null&&id.matches("[A-Za-z0-9_-]{1,64}");}
 static boolean basename(String name){return name!=null&&name.length()<=100&&name.matches("[A-Za-z0-9][A-Za-z0-9._-]*")&&!name.contains("..");}
 private Path input(String name)throws IOException{if(!basename(name))throw new IOException("invalid_file");if(Files.isSymbolicLink(root)||Files.isSymbolicLink(inbox))throw new IOException("symlink_rejected");Path path=inbox.resolve(name);if(!path.toFile().getCanonicalFile().getParentFile().equals(inbox.toFile().getCanonicalFile())||!Files.isRegularFile(path,LinkOption.NOFOLLOW_LINKS)||Files.isSymbolicLink(path))throw new IOException("invalid_file");return path;}
 boolean exists(String name)throws IOException{if(!basename(name))throw new IOException("invalid_file");return Files.exists(inbox.resolve(name),LinkOption.NOFOLLOW_LINKS);}
 byte[] read(String name,int limit)throws IOException{Path path=input(name);if(Files.size(path)>limit)throw new IOException("too_large");try(InputStream in=Files.newInputStream(path,LinkOption.NOFOLLOW_LINKS);ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] buffer=new byte[8192];int n,total=0;while((n=in.read(buffer))!=-1){total+=n;if(total>limit)throw new IOException("too_large");out.write(buffer,0,n);}return out.toByteArray();}}
 interface CommitHistory{void commit()throws Exception;}
 void stageAndConsume(String name,Path privateDir,String id,byte[] bytes,CommitHistory history)throws Exception{if(!requestId(id))throw new IOException("invalid_request_id");atomic(privateDir,id+".bin",bytes);history.commit();consume(name);}
 void consume(String name)throws IOException{Path path=input(name);Files.delete(path);}
 static String hash(String command,byte[] bytes)throws Exception{MessageDigest digest=MessageDigest.getInstance("SHA-256");digest.update(command.getBytes(StandardCharsets.US_ASCII));digest.update((byte)0);return IdentityProof.hex(digest.digest(bytes));}
 static String mime(String name,byte[] bytes)throws IOException{String lower=name.toLowerCase(Locale.ROOT),mime=null;if(bytes.length>=8&&bytes[0]==(byte)137&&bytes[1]==80&&bytes[2]==78&&bytes[3]==71&&bytes[4]==13&&bytes[5]==10&&bytes[6]==26&&bytes[7]==10)mime="image/png";else if(bytes.length>=3&&bytes[0]==(byte)255&&bytes[1]==(byte)216&&bytes[2]==(byte)255)mime="image/jpeg";else if(bytes.length>=6&&(new String(bytes,0,6,StandardCharsets.US_ASCII).equals("GIF87a")||new String(bytes,0,6,StandardCharsets.US_ASCII).equals("GIF89a")))mime="image/gif";else if(bytes.length>=12&&new String(bytes,0,4,StandardCharsets.US_ASCII).equals("RIFF")&&new String(bytes,8,4,StandardCharsets.US_ASCII).equals("WEBP"))mime="image/webp";
  if(mime==null||!(mime.equals("image/png")&&lower.endsWith(".png")||mime.equals("image/jpeg")&&(lower.endsWith(".jpg")||lower.endsWith(".jpeg"))||mime.equals("image/gif")&&lower.endsWith(".gif")||mime.equals("image/webp")&&lower.endsWith(".webp")))throw new IOException("invalid_image");return mime;}
 void result(String id,String json)throws IOException{if(!requestId(id))throw new IOException("invalid_request_id");atomic(outbox,"result-"+id+".json",json.getBytes(StandardCharsets.UTF_8));}
 void status(String json)throws IOException{atomic(outbox,"status.json",json.getBytes(StandardCharsets.UTF_8));}
 static void atomic(Path directory,String name,byte[] bytes)throws IOException{if(Files.isSymbolicLink(directory)||!Files.isDirectory(directory,LinkOption.NOFOLLOW_LINKS))throw new IOException("invalid_directory");Path target=directory.resolve(name),temp=directory.resolve(".pending-"+UUID.randomUUID()+".tmp");try{try(OutputStream out=Files.newOutputStream(temp,StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS)){out.write(bytes);}Files.move(temp,target,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);}finally{Files.deleteIfExists(temp);}}
}
