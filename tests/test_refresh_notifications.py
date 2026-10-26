import asyncio
import pytest
from server.app import Hub

class Adapter:
    async def close(self): pass

@pytest.mark.asyncio
async def test_last_update_during_refresh_is_drained_once(tmp_path):
    hub=Hub(tmp_path,Adapter());entered=asyncio.Event();release=asyncio.Event();again=asyncio.Event();calls=0
    async def refresh():
        nonlocal calls
        calls+=1
        if calls==1:
            entered.set();await release.wait()
        else:again.set()
    hub.refresh=refresh
    try:
        await hub.changed()
        await asyncio.wait_for(entered.wait(),1)
        original=hub.refresh_pending
        for _ in range(1000):await hub.changed()
        assert hub.refresh_pending is original
        release.set()
        await asyncio.wait_for(again.wait(),1)
        await asyncio.wait_for(hub.refresh_pending,1)
        assert calls==2
    finally:release.set();await hub.close()

@pytest.mark.asyncio
async def test_close_cancels_queued_refresh(tmp_path):
    hub=Hub(tmp_path,Adapter());calls=0
    async def refresh():
        nonlocal calls
        calls+=1
    hub.refresh=refresh
    await hub.changed();await hub.close();await asyncio.sleep(.1)
    assert calls==0
