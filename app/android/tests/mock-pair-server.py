# Synthetic loopback-only fixture. Never deploy or use as a real pairing server.
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import json,time,secrets
counts={}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  if self.path=='/test-metrics':
   raw=json.dumps(counts).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
  time.sleep(5)
  raw=json.dumps({'error':{'code':'unauthorized','message':'test stale connection'}}).encode();self.send_response(401);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def do_POST(self):
  counts[self.path]=counts.get(self.path,0)+1
  body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  if self.path=='/pair/claim':data={'request_id':'native-test-claim','poll_token':'native-test-poll-secret-1234567890','phrase':'TEST ONLY','expires_at':time.time()+120}
  elif self.path=='/pair/status':
   time.sleep(1)
   data={'status':'approved','token':'native-test-device-token-1234567890','device_id':'native-test-device','server_name':'Native Test Mock'}
  else:data={}
  raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
ThreadingHTTPServer(('127.0.0.1',32869),Handler).serve_forever()
