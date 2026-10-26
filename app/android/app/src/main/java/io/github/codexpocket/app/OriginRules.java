package io.github.codexpocket.app;
import java.net.*;
import java.util.*;
final class OriginRules {
 static String normalize(String raw,boolean debug)throws Exception{
  URI u=new URI(raw.trim());String host=u.getHost();
  if(host==null||u.getUserInfo()!=null||u.getRawQuery()!=null||u.getRawFragment()!=null||u.getPort()>65535||u.getPort()==0||!(u.getRawPath()==null||u.getRawPath().isEmpty()||u.getRawPath().equals("/")))throw new IllegalArgumentException("请输入不含路径、凭据或参数的服务地址");
  String scheme=u.getScheme()==null?"":u.getScheme().toLowerCase(Locale.ROOT);host=host.toLowerCase(Locale.ROOT);if(host.startsWith("["))host=host.substring(1,host.length()-1);
  if(host.contains("%"))throw new IllegalArgumentException("不支持带区域标识的地址");
  boolean ipv6=host.contains(":");if(ipv6)host=ipv6(host);else host=IDN.toASCII(host,IDN.USE_STD3_ASCII_RULES).toLowerCase(Locale.ROOT);
  if(!scheme.equals("https")&&!(debug&&scheme.equals("http")&&privateHost(host)))throw new IllegalArgumentException("需要 HTTPS；开发版明文仅限显式私网地址");
  int port=u.getPort();if(port==(scheme.equals("https")?443:80))port=-1;
  return scheme+"://"+(ipv6?"["+host+"]":host)+(port<0?"":":"+port);
 }
 static boolean privateHost(String host){
  if(host.equals("localhost")||host.equals("::1"))return true;
  if(host.contains(":")){try{byte[] b=InetAddress.getByName(host).getAddress();return b.length==16&&(b[0]&0xfe)==0xfc;}catch(Exception e){return false;}}
  if(!host.matches("[0-9]+(?:\\.[0-9]+){3}"))return false;String[] parts=host.split("\\.");int[] a=new int[4];
  for(int i=0;i<4;i++){if(parts[i].length()>1&&parts[i].startsWith("0"))return false;try{a[i]=Integer.parseInt(parts[i]);}catch(Exception e){return false;}if(a[i]>255)return false;}
  return a[0]==127||a[0]==10||(a[0]==192&&a[1]==168)||(a[0]==172&&a[1]>=16&&a[1]<=31)||(a[0]==100&&a[1]>=64&&a[1]<=127);
 }
 private static String ipv6(String host)throws Exception{
  if(host.contains("."))throw new IllegalArgumentException("请使用标准IPv6地址");byte[] bytes=InetAddress.getByName(host).getAddress();if(bytes.length!=16)throw new IllegalArgumentException("IPv6地址无效");
  int[] w=new int[8];for(int i=0;i<8;i++)w[i]=((bytes[2*i]&255)<<8)|(bytes[2*i+1]&255);int best=-1,length=0;
  for(int i=0;i<8;){if(w[i]!=0){i++;continue;}int end=i;while(end<8&&w[end]==0)end++;if(end-i>length){best=i;length=end-i;}i=end;}
  if(length<2)best=-1;StringBuilder out=new StringBuilder();for(int i=0;i<8;){if(i==best){out.append("::");i+=length;continue;}if(out.length()>0&&out.charAt(out.length()-1)!=':')out.append(':');out.append(Integer.toHexString(w[i++]));}return out.toString();
 }
}
