package io.github.codexpocket.app;
/** Application HTTP responses do not establish transport failure. */
final class HttpFailurePolicy {
 static boolean gatewayAuthentication(int status,boolean deviceRevoked){return status==401&&!deviceRevoked;}
}
