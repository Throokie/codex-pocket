package io.github.codexpocket.app;
/** Late work may reject itself; only an authoritative empty store may clear current routes. */
final class RoutingGrantPolicy {
 enum Action{REJECT,CLEAR,KEEP,REPLACE}
 static Action decide(boolean sourcePresent,boolean sourceMatches,boolean currentPresent,long sourceEpoch,long routeEpoch){
  if(!sourcePresent)return currentPresent?Action.REJECT:Action.CLEAR;
  if(!sourceMatches)return Action.REJECT;
  return sourceEpoch==routeEpoch?Action.KEEP:Action.REPLACE;
 }
}
