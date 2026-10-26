import asyncio
import copy
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'adapters/desktop.py'
spec = importlib.util.spec_from_file_location('pocket_desktop', MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CMD = {'id': 33, 'method': 'item/commandExecution/requestApproval', 'params': {
    'threadId': 'thread-one', 'turnId': 'turn-one', 'itemId': 'cmd-one',
    'command': 'printf harmless', 'cwd':'/safe/workspace', 'reason': 'test only'}}
QUESTION = {'id': 'q-native', 'method': 'item/tool/requestUserInput', 'params': {
    'threadId': 'thread-one', 'turnId': 'turn-one', 'itemId': 'q-one', 'questions': [
        {'id': 'color', 'question': 'Select?', 'header': 'Choice',
         'options': [{'label': 'A', 'description': 'First'}, {'label': 'B', 'description': 'Second'}]}]}}


class FakeDesktop:
    def __init__(self, path):
        self.path = path
        self.requests = [copy.deepcopy(CMD), copy.deepcopy(QUESTION)]
        self.revision = 1
        self.owner = 'desktop-owner'
        self.mode = 'apply'
        self.calls = []
        self.writers = []
        self.server = None

    async def start(self):
        self.server = await asyncio.start_unix_server(self.client, self.path)

    async def close(self):
        for writer in self.writers:
            writer.close()
        self.server.close()
        await self.server.wait_closed()

    async def write(self, writer, frame):
        data = json.dumps(frame).encode()
        writer.write(struct.pack('<I', len(data)) + data)
        await writer.drain()

    async def snapshot(self, writer):
        await self.write(writer, {'type': 'broadcast', 'sourceClientId': self.owner, 'version': 11,
            'method': 'thread-stream-state-changed', 'params': {'hostId': 'local',
            'conversationId': 'thread-one', 'change': {'type': 'snapshot', 'revision': self.revision,
            'conversationState': {'requests': copy.deepcopy(self.requests), 'turns': [{'secret': 'NEVER RETAIN'}],
                                  'reasoning': 'NEVER RETAIN', 'threadRuntimeStatus': {'type': 'idle'}}}}})

    async def client(self, reader, writer):
        self.writers.append(writer)
        try:
            while True:
                n = struct.unpack('<I', await reader.readexactly(4))[0]
                frame = json.loads(await reader.readexactly(n))
                self.calls.append(copy.deepcopy(frame))
                if frame.get('type') == 'request':
                    method = frame['method']
                    result = {}
                    if method == 'initialize':
                        result = {'clientId': 'bridge-client'}
                    elif method == 'thread-owner-discovery':
                        result = {'supportsUntrustedAppInput': True}
                    elif method in mod.SUPPORTED_REQUESTS.values():
                        if self.mode == 'apply':
                            self.requests = [r for r in self.requests if r['id'] != frame['params']['requestId']]
                            self.revision += 1
                            await self.snapshot(writer)
                        elif self.mode == 'owner-change':
                            await self.write(writer, {'type': 'broadcast', 'method': 'client-status-changed',
                                'params': {'clientId': self.owner, 'status': 'disconnected'}})
                        result = {'ok': True}
                    await self.write(writer, {'type': 'response', 'requestId': frame['requestId'],
                        'resultType': 'success', 'method': method, 'handledByClientId': self.owner, 'result': result})
                elif frame.get('method') == 'thread-stream-following-changed' and frame['params']['following']:
                    await self.snapshot(writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()


class Adapter(mod.DesktopAdapter):
    async def _tool(self, name, args):
        self._catalog = {'read_thread': {}, 'send_message_to_thread': {}}
        if name == 'list_threads':
            return {'threads': [{'id': 'thread-one', 'title': 'Test', 'kind': 'codex', 'hostId': 'local', 'status': 'idle'}]}
        if name == 'read_thread':
            return {'turns': [{'id': 't', 'startedAt': 123, 'items': [
                {'id': 'u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Hello'}]},
                {'id': 'secret', 'type': 'agentMessage', 'phase': 'analysis', 'text': 'NEVER EXPOSE'},
                {'id': 'tool', 'type': 'functionCallOutput', 'output': {'password': 'NEVER EXPOSE'}},
                {'id': 'a', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Visible'}]}]}
        if name == 'send_message_to_thread':
            return {'threadId': args['threadId']}
        raise AssertionError(name)


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pocket-ipc-test-')
        self.fake = FakeDesktop(str(Path(self.tmp.name) / 'ipc.sock'))
        await self.fake.start()
        self.changed = 0
        self.adapter = Adapter('caller', ipc_path=self.fake.path)
        async def callback():
            self.changed += 1
        await self.adapter.start(callback)
        for _ in range(200):
            if self.adapter._revisions.get('thread-one'):
                break
            await asyncio.sleep(.01)
        self.assertIn('thread-one', self.adapter._revisions)

    async def asyncTearDown(self):
        await self.adapter.close()
        await self.fake.close()
        self.tmp.cleanup()

    async def test_follower_projection_and_visible_messages(self):
        result = await self.adapter.snapshot('thread-one', 30)
        self.assertEqual([m['text'] for m in result['messages']], ['Hello', 'Visible'])
        self.assertNotIn('NEVER', json.dumps(result))
        self.assertEqual(set(self.adapter._native['thread-one']), {'requests'})
        self.assertEqual(len(result['requests']), 2)
        question = next(r for r in result['requests'] if r['type'] == 'user_input')
        self.assertEqual(question['questions'][0]['options'][0]['id'], '0')
        self.assertTrue(result['capabilities']['live'])
        self.assertTrue(all(c.get('method') != 'thread/resume' for c in self.fake.calls))

    async def test_command_confirmation_requires_removal(self):
        result = await self.adapter.snapshot('thread-one')
        request = next(r for r in result['requests'] if r['type'] == 'command_approval')
        self.assertEqual(await self.adapter.answer(request['id'], 'accept'), 'delivered')
        sent = [c for c in self.fake.calls if c.get('method') == 'thread-follower-command-approval-decision']
        self.assertEqual(sent[0]['params']['requestId'], 33)
        self.assertEqual(sent[0]['targetClientId'], 'desktop-owner')
        self.assertNotIn('hostId', sent[0])
        with self.assertRaises(mod.DesktopError):
            await self.adapter.answer(request['id'], 'accept')

    async def test_ok_without_snapshot_is_unknown(self):
        self.fake.mode = 'noop'
        result = await self.adapter.snapshot('thread-one')
        self.assertEqual(await self.adapter.answer(result['requests'][0]['id'], 'accept'), 'unknown')
        self.assertEqual(len(self.fake.requests), 2)

    async def test_owner_disappearance_is_unknown(self):
        self.fake.mode = 'owner-change'
        result = await self.adapter.snapshot('thread-one')
        self.assertEqual(await self.adapter.answer(result['requests'][0]['id'], 'accept'), 'unknown')
        self.assertNotIn('thread-one', self.adapter._owners)

    async def test_native_question_response(self):
        result = await self.adapter.snapshot('thread-one')
        request = next(r for r in result['requests'] if r['type'] == 'user_input')
        self.assertEqual(await self.adapter.answer(request['id'], answers={'color': ['B']}), 'delivered')
        sent = next(c for c in self.fake.calls if c.get('method') == 'thread-follower-submit-user-input')
        self.assertEqual(sent['params']['response'], {'answers': {'color': {'answers': ['B']}}})

    async def test_missing_question_rejected_before_write(self):
        result = await self.adapter.snapshot('thread-one')
        request = next(r for r in result['requests'] if r['type'] == 'user_input')
        with self.assertRaises(mod.DesktopError):
            await self.adapter.answer(request['id'], answers={'other': ['B']})
        self.assertFalse(any(c.get('method') == 'thread-follower-submit-user-input' for c in self.fake.calls))

    async def test_patch_projection_filters_unrelated_native_state(self):
        await self.adapter._broadcast({'type': 'broadcast', 'sourceClientId': 'desktop-owner', 'version': 11,
            'method': 'thread-stream-state-changed', 'params': {'hostId': 'local', 'conversationId': 'thread-one',
                'change': {'type': 'patches', 'baseRevision': 1, 'revision': 2, 'patches': [
                    {'op': 'replace', 'path': ['reasoning'], 'value': 'SECRET'},
                    {'op': 'remove', 'path': ['requests', 0]}]}}})
        self.assertEqual(len(self.adapter._native['thread-one']['requests']), 1)
        self.assertEqual(set(self.adapter._native['thread-one']), {'requests'})

    async def test_secret_question_is_not_sent_to_phone(self):
        self.adapter._native['thread-one']['requests'][1]['params']['questions'][0]['isSecret'] = True
        result = await self.adapter.snapshot('thread-one')
        self.assertEqual(len(result['requests']), 2)
        secret = next(r for r in result['requests'] if r['type'] == 'desktop_only')
        self.assertEqual(secret['questions'], [])
        self.assertEqual(secret['options'], [])
        with self.assertRaises(mod.DesktopError):
            await self.adapter.answer(secret['id'], answers={'color':['secret']})

    async def test_text_send_keeps_native_tool_channel(self):
        self.assertEqual(await self.adapter.send('thread-one', 'Example'), 'delivered')
        with self.assertRaises(mod.DesktopError):
            await self.adapter.send('arbitrary-thread', 'Example')

    async def test_concurrent_devices_dispatch_only_once(self):
        result = await self.adapter.snapshot('thread-one')
        request = result['requests'][0]
        answers = await asyncio.gather(self.adapter.answer(request['id'], 'accept'),
                                       self.adapter.answer(request['id'], 'decline'), return_exceptions=True)
        self.assertEqual(sum(answer == 'delivered' for answer in answers), 1)
        self.assertEqual(sum(isinstance(answer, mod.DesktopError) for answer in answers), 1)
        self.assertEqual(sum(c.get('method') == 'thread-follower-command-approval-decision' for c in self.fake.calls), 1)

    async def test_changed_request_body_invalidates_old_card(self):
        result = await self.adapter.snapshot('thread-one')
        old_id = result['requests'][0]['id']
        self.adapter._native['thread-one']['requests'][0]['params']['command'] = 'changed command'
        with self.assertRaises(mod.DesktopError):
            await self.adapter.answer(old_id, 'accept')

    async def test_only_known_delegation_inputs_become_user_messages(self):
        data = {'turns': [{'id':'t', 'items': [
            {'id':'d1', 'type':'functionCallOutput', 'namespace':'codex_app', 'name':'send_message_to_thread',
             'output': {'text':'<codex_delegation><source_thread_id>source</source_thread_id><input>Hello &amp; image</input></codex_delegation>'}},
            {'id':'d2', 'type':'functionCallOutput', 'namespace':'other', 'name':'send_message_to_thread',
             'output': {'text':'<codex_delegation><input>NEVER EXPOSE</input></codex_delegation>'}},
            {'id':'d3', 'type':'functionCallOutput', 'namespace':'codex_app', 'name':'read_thread',
             'output': {'text':'<codex_delegation><input>NEVER EXPOSE</input></codex_delegation>'}}]}]}
        messages = mod._visible_messages(data, 30)
        self.assertEqual([(m['role'],m['text']) for m in messages], [('user','Hello & image')])

    async def test_unknown_pending_request_is_read_only_card(self):
        self.adapter._native['thread-one']['requests'].append({'id':'perm','method':'item/permissions/requestApproval',
            'params':{'threadId':'thread-one','turnId':'turn-one','permissions':{'network':{'enabled':True}}}})
        data = await self.adapter.snapshot('thread-one')
        request = next(r for r in data['requests'] if r['type'] == 'desktop_only')
        with self.assertRaises(mod.DesktopError):
            await self.adapter.answer(request['id'], 'accept')
        self.assertEqual(request['options'], [])

    async def test_file_approval_without_diff_is_not_actionable(self):
        self.adapter._native['thread-one']['requests'].append({'id':'file','method':'item/fileChange/requestApproval',
            'params':{'threadId':'thread-one','turnId':'turn-one','itemId':'file-one','reason':'A vague reason'}})
        data = await self.adapter.snapshot('thread-one')
        request = next(r for r in data['requests'] if r['type'] == 'desktop_only')
        self.assertEqual(request['options'], [])
        with self.assertRaises(mod.DesktopError):
            await self.adapter.answer(request['id'], 'accept')

    async def test_images_never_start_another_engine(self):
        self.adapter._thread_status['thread-one'] = {'type': 'active'}
        with self.assertRaises(mod.DesktopError) as error:
            await self.adapter.send('thread-one', '', [{'path': '/not/a/file'}])
        self.assertEqual(error.exception.code, 'thread_busy')



class AsyncProjectionTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self):
        a=mod.DesktopAdapter('caller');a._owners['t']='owner';a._revisions['t']=1;a._connected.set();a._thread_status['t']={'type':'idle'};a._threads=[{'id':'t','title':'Real title','status':'idle'}];return a
    def state(self, answered=False):
        items=[{'id':'agent','type':'agentMessage','questions':[{'title':'Pick exactly?', 'options':['A','B']}], 'secret_tool':'discard'}]
        if answered:
            qid=json.dumps(['request_user_input_async','agent',0],separators=(',',':'))
            reply='<send_user_message_question_reply>\n'+json.dumps([{'questionItemId':qid,'question':'Pick exactly?','answer':'B'}])+'\n</send_user_message_question_reply>'
            items.append({'type':'userMessage','content':[{'type':'text','text':reply}]})
        return {'turnHistory':{'history':{'entitiesByKey':{'tail':{'turnId':'turn','items':items}}}},'turns':[]}
    async def test_projection_and_exact_envelope_confirmation(self):
        a=self.adapter();a._project_async_snapshot('t',self.state());r=a._async_requests()[0]
        self.assertEqual(r['type'],'question');self.assertEqual(r['thread_title'],'Real title')
        self.assertNotIn('secret_tool',repr(a._async_items));self.assertEqual(r['questions'][0]['options'][1]['label'],'B')
        calls=[]
        async def native(method,args,**kwargs):
            self.assertEqual(method,'thread-follower-start-turn');self.assertEqual(kwargs['version'],2)
            self.assertEqual(args['turnStart']['context'],{'inheritThreadSettings':True})
            calls.append(args);a._project_async_snapshot('t',self.state(True));return {'resultType':'success'}
        async def refresh(tid):pass
        a._request=native;a._resnapshot=refresh
        self.assertEqual(await a.answer(r['id'],answers={r['questions'][0]['id']:['B']}),'delivered')
        envelope=calls[0]['turnStart']['request']['input'][0]['text'];self.assertIn('Pick exactly?',envelope);self.assertIn('request_user_input_async',envelope)
        self.assertEqual(a._async_requests(),[])
        with self.assertRaises(mod.DesktopError):await a.answer(r['id'],answers={r['questions'][0]['id']:['B']})
    async def test_owner_change_stales_identity_and_no_arbitrary_answer(self):
        a=self.adapter();a._project_async_snapshot('t',self.state());r=a._async_requests()[0]
        with self.assertRaises(mod.DesktopError):await a.answer(r['id'],answers={'wrong':['B']})
        a._owners['t']='different'
        with self.assertRaises(mod.DesktopError):await a.answer(r['id'],answers={r['questions'][0]['id']:['B']})
    async def test_token_delta_does_not_resnapshot(self):
        a=self.adapter();a._project_patch('t',{'op':'replace','path':['turnHistory','history','entitiesByKey','tail','items',0,'text'],'value':'token'})
        self.assertFalse(a._projection_tasks)

if __name__ == '__main__':
    unittest.main(verbosity=2)

class MarkdownImageTests(unittest.TestCase):
    def test_only_absolute_explicit_images(self):
        result={'turns':[{'items':[{'id':'a','type':'agentMessage','text':'![pic](/allowed/a.png) ![remote](https://host/a.png) ![relative](a.png) ![file](file:///a.png) ![spaced](</allowed/a b.webp>)'}]}]}
        images=mod._visible_messages(result,30)[0]['images']
        self.assertEqual(images,[{'_path':'/allowed/a.png'},{'_path':'/allowed/a b.webp'}])

class AsyncBusyTests(AsyncProjectionTests):
    async def test_busy_never_dispatches(self):
        a=self.adapter();a._thread_status['t']={'type':'active'};a._project_async_snapshot('t',self.state());r=a._async_requests()[0]
        with self.assertRaises(mod.DesktopError) as result:
            await a.answer(r['id'],answers={r['questions'][0]['id']:['B']})
        self.assertEqual(result.exception.code,'thread_busy');self.assertFalse(a._async_sent)

class CommandContextTests(unittest.TestCase):
    def test_command_shows_complete_scope(self):
        a=mod.DesktopAdapter('caller');item=copy.deepcopy(CMD)
        item['params']['networkApprovalContext']={'host':'example.test','protocol':'https'}
        card=a._normalize_request('thread-one',item)
        self.assertEqual(card['type'],'command_approval')
        for text in ['printf harmless','/safe/workspace','test only','example.test']:
            self.assertIn(text,card['body'])
        self.assertEqual(card['options'][0]['id'],'accept')
    def test_missing_cwd_and_oversized_scope_are_desktop_only(self):
        a=mod.DesktopAdapter('caller');item=copy.deepcopy(CMD);del item['params']['cwd']
        self.assertEqual(a._normalize_request('thread-one',item)['type'],'desktop_only')
        item=copy.deepcopy(CMD);item['params']['reason']='r'*mod.MAX_TEXT
        card=a._normalize_request('thread-one',item)
        self.assertEqual(card['type'],'desktop_only');self.assertEqual(card['options'],[])

class VisibleOrdering(unittest.TestCase):
    def test_newest_first_native_history_is_presented_chronologically(self):
        data={'turns':[{'id':'new','startedAt':2,'items':[{'id':'b','type':'agentMessage','text':'new','phase':'final_answer'}]},{'id':'old','startedAt':1,'items':[{'id':'a','type':'agentMessage','text':'old','phase':'final_answer'}]}]}
        self.assertEqual([m['text'] for m in mod._visible_messages(data,30)],['old','new'])


class ReadbackScheduling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import patch
        self.patches=[patch.object(mod,'NOTIFY_DELAY',.001),patch.object(mod,'MESSAGE_MIN_INTERVAL',.001),patch.object(mod,'RECONCILE_DELAYS',(.02,.03,.04))]
        for p in self.patches:p.start()
        self.a=mod.DesktopAdapter('caller');self.a._controller_id='a'
        self.a._threads=[{'id':tid,'title':tid,'status':'idle'} for tid in ('a','b')]
        self.a._connected.set();self.a._owners={'a':'owner'};self.a._revisions={'a':1};self.a._following={'a'}
        self.reads=[]
        async def discover(tid):return False
        self.a._discover=discover
    async def asyncTearDown(self):
        await self.a.close()
        for p in reversed(self.patches):p.stop()
    def source(self,text):
        return {'turns':[{'id':'turn','items':[{'id':'visible','type':'agentMessage','phase':'final_answer','text':text}]}]}
    async def event(self):
        revision=self.a._revisions['a']
        await self.a._broadcast({'type':'broadcast','method':'thread-stream-state-changed','version':11,'sourceClientId':'owner',
            'params':{'hostId':'local','conversationId':'a','change':{'type':'patches','baseRevision':revision,'revision':revision+1,
                'patches':[{'op':'replace','path':['turns',0,'items',0,'text'],'value':'visible update'}]}}})
    async def test_final_event_during_callback_tail_is_drained_by_same_task(self):
        mod.RECONCILE_DELAYS=(10,10,10)
        entered,release,done=asyncio.Event(),asyncio.Event(),asyncio.Event();final=[False];calls=[]
        async def tool(name,args):
            self.reads.append(args['threadId']);return self.source('final' if final[0] else 'old')
        async def callback():
            calls.append(1);await self.a.snapshot('a')
            if len(calls)==1:entered.set();await release.wait()
            else:done.set()
        self.a._tool=tool;self.a._callback=callback
        await self.a._notify();await asyncio.wait_for(entered.wait(),.2);task=self.a._change_task
        final[0]=True;await self.event()
        self.assertIs(self.a._change_task,task)
        release.set();await asyncio.wait_for(done.wait(),.2)
        self.assertEqual(self.a._messages[0]['text'],'final');self.assertEqual(len(calls),2)
    async def test_quiet_reads_recover_final_indexed_after_last_native_event(self):
        first,final_seen=asyncio.Event(),asyncio.Event();indexed=[False]
        async def tool(name,args):
            self.reads.append(args['threadId']);first.set();return self.source('final' if indexed[0] else 'old index')
        async def callback():
            snap=await self.a.snapshot('a')
            if snap['messages'] and snap['messages'][0]['text']=='final':final_seen.set()
        self.a._tool=tool;self.a._callback=callback
        await self.event();await asyncio.wait_for(first.wait(),.2)
        indexed[0]=True
        await asyncio.wait_for(final_seen.wait(),.3);await asyncio.sleep(.15)
        self.assertEqual(len(self.reads),4)  # initial event + exactly three quiet reconciliations
        self.assertIsNone(self.a._reconcile_task);self.assertFalse(self.a._messages_dirty)
    async def test_continuous_events_share_one_quiet_task_and_three_tail_callbacks(self):
        calls=[]
        async def callback():calls.append(1)
        self.a._callback=callback
        self.a._queue_reconcile();task=self.a._reconcile_task
        for _ in range(40):
            self.a._queue_reconcile();self.assertIs(self.a._reconcile_task,task);await asyncio.sleep(.001)
        self.assertEqual(calls,[])
        await asyncio.sleep(.15)
        self.assertEqual(len(calls),3);self.assertIsNone(self.a._reconcile_task)
    async def test_controller_switch_cancels_old_quiet_and_close_cancels_new(self):
        calls=[]
        async def tool(name,args):return self.source(args['threadId'])
        async def callback():calls.append(self.a._controller_id)
        self.a._tool=tool;self.a._callback=callback
        self.a._queue_reconcile();old=self.a._reconcile_task
        await self.a.snapshot('b');await asyncio.sleep(0)
        self.assertTrue(old.cancelled());new=self.a._reconcile_task
        await self.a.close();count=len(calls);await asyncio.sleep(.15)
        self.assertTrue(new.cancelled());self.assertEqual(len(calls),count)
    async def test_old_controller_read_cannot_overwrite_new_controller(self):
        entered,release,selected=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def tool(name,args):
            if args['threadId']=='a':entered.set();await release.wait();return self.source('old-controller')
            return self.source('new-controller')
        async def discover(tid):selected.set();return False
        self.a._tool=tool;self.a._discover=discover
        old=asyncio.create_task(self.a.snapshot('a'));await entered.wait()
        new=asyncio.create_task(self.a.snapshot('b'));await selected.wait()
        release.set();old_result,new_result=await asyncio.gather(old,new)
        self.assertNotIn('old-controller',repr(old_result['messages']))
        self.assertEqual(new_result['messages'][0]['text'],'new-controller')
        self.assertEqual(self.a._messages[0]['text'],'new-controller')
    async def test_non_json_or_unknown_schema_keeps_cache_dirty_without_retry_loop(self):
        cached={'id':'cache','role':'assistant','text':'cached','images':[],'created_at':0}
        self.a._messages=[cached]
        async def tool(name,args):self.reads.append(1);return {'text':'non-json response'}
        self.a._tool=tool
        result=await self.a.snapshot('a');await asyncio.sleep(.1)
        self.assertEqual(result['messages'],[cached]);self.assertTrue(self.a._messages_dirty)
        self.assertEqual(len(self.reads),1);self.assertTrue(self.a.last_error)
        for value in ({'turns':[{'items':[]},{}]}, {'turns':[{'items':[{'type':'assistantMessage','content':[]}]}]}, {'turns':[{'items':[{'type':'agentMessage','text':{}}]}]}):
            with self.assertRaises(mod.DesktopError):mod._visible_messages(value,30)
    async def test_native_readiness_requires_controller_follow_and_revision(self):
        self.a._following.clear();self.a._messages_dirty=False;self.a._messages_at=mod.time.monotonic()-6
        async def tool(name,args):self.reads.append(1);return self.source('read fallback')
        self.a._tool=tool
        snap=await self.a.snapshot('a')
        self.assertTrue(self.a._connected.is_set());self.assertFalse(snap['capabilities']['live'])
        self.assertEqual(len(self.reads),1)
        self.a._following.add('a');self.assertTrue(self.a._native_ready('a'))
        self.a._revisions.clear();self.assertFalse(self.a._native_ready('a'))
