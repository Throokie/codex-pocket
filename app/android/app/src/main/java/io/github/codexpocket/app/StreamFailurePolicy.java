package io.github.codexpocket.app;
/** SSE application errors and intentional wake-ups never downgrade transport health. */
final class StreamFailurePolicy {
 static long nextBackoff(long current){return Math.min(15000,current*2);}
 static boolean shouldProbe(int receivedStatus,boolean sameWake,boolean running){return running&&sameWake&&(receivedStatus<0||receivedStatus==200);}
}
