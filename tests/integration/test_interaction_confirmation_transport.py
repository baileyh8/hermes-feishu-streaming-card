"""Lost HTTP response followed by delayed read-only confirmation; never replay POST."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hermes_feishu_card import hook_runtime as runtime


def test_lost_response_and_late_visibility_do_not_duplicate_the_interaction(tmp_path, monkeypatch):
    state={'posts':0,'gets':0,'visible_at':float('inf'),'confirmed':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            event=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path=='/events' and event['event']=='interaction.requested'
            state['posts']+=1
            state['visible_at']=time.monotonic()+.15
            # The event is accepted, but the HTTP response is lost before the
            # separately delivered card can be discovered by the client.
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        def do_GET(self):
            assert self.path=='/interactions/late-http'
            state['gets']+=1
            if time.monotonic()<state['visible_at']:
                result={'ok':False,'status':'not_found'}
            elif not state['confirmed']:
                result={'ok':True,'status':'pending','interaction_id':'late-http'}
                state['confirmed']=True
            else:
                result={'ok':True,'status':'completed','interaction_id':'late-http','choice':'A'}
            body=json.dumps(result).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('HERMES_FEISHU_CARD_STATE_DIR',str(tmp_path/'state'))
    monkeypatch.setenv('HERMES_FEISHU_CARD_ENABLED','true')
    monkeypatch.setenv('HERMES_FEISHU_CARD_EVENT_URL',f'http://127.0.0.1:{server.server_port}/events')
    monkeypatch.setattr(runtime,'_fetch_delivery_policy_sync',lambda *a,**k:{'ok':True,'disposition':'card','ttl_ms':1000})
    runtime.reset_runtime_state()
    try:
        result=runtime.request_interaction_from_hermes_locals(
            {'chat_id':'fixture-chat','message_id':'fixture-source'},kind='clarify',
            interaction_id='late-http',prompt='Fixture?',options=[{'label':'A','value':'A'}],
            timeout_seconds=2,poll_interval_seconds=0)
        assert result and result['choice']=='A'
        assert state['posts']==1 and state['gets']>=3
    finally:
        runtime.reset_runtime_state()
        server.shutdown();server.server_close();thread.join(timeout=2)
