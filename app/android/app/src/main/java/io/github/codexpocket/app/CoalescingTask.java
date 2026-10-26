package io.github.codexpocket.app;

import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;

/** At most one worker and one dirty bit, even during an arbitrarily long request. */
final class CoalescingTask {
    private final Executor executor;
    private final Runnable task;
    private boolean running,dirty,closed;
    CoalescingTask(Executor executor,Runnable task){this.executor=executor;this.task=task;}
    synchronized void trigger(){
        if(closed)return;dirty=true;if(running)return;running=true;
        try{executor.execute(this::drain);}catch(RejectedExecutionException e){running=false;dirty=false;}
    }
    private void drain(){
        while(true){synchronized(this){if(closed||!dirty){running=false;return;}dirty=false;}
            try{task.run();}catch(RuntimeException ignored){/* The next real event may retry; no automatic loop. */}
        }
    }
    synchronized void close(){closed=true;dirty=false;}
}
