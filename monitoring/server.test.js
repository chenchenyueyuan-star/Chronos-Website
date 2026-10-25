'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');const {createServer}=require('node:http');
const {createMonitorServer}=require('./server');

test('Authenticated lifecycle, frame measurements, isolation, pause/resume, and replay',async()=>{
 const events=[];let nextRun=0, sdk, failMetrics=1, failFinal=1, failPause=1;
 const receiver=createServer(async(req,res)=>{let data='';for await(const c of req)data+=c;const token=req.headers.authorization;if(!['Bearer alice','Bearer bob'].includes(token)){res.writeHead(403);res.end('{}');return;}
 const payload=JSON.parse(data);events.push({path:req.url,token,payload});if(req.url==='/metrics'&&failMetrics-->0){res.writeHead(500);res.end('{}');return;}if(req.url==='/event'&&payload.kind==='paused'&&failPause-->0){res.writeHead(500);res.end('{}');return;}if(req.url==='/event'&&payload.kind==='completed'&&failFinal-->0){res.writeHead(500);res.end('{}');return;}res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/start'?{run_id:payload.run_id||'run-'+ ++nextRun}:{ok:true}));});
 await new Promise(r=>receiver.listen(0,'127.0.0.1',r));
 class FakeSDK extends EventEmitter{constructor(){super();this.processingStatus=1;sdk=this;}useCustomInput(){}start(){this.processingStatus=3;}async stopAsync(){this.processingStatus=1;}reset(){this.processingStatus=1;}async destroy(){}sendFrame(){this.emit('metrics',{cardio:{pulseRate:[{value:72,timestamp:123,confidence:.9,stable:true}],waveform:['must-not-be-forwarded']},breathing:{rate:[]},face:{expression:[{timestamp:123,stable:true,scores:[{type:5,confidence:78}]}],landmarks:['private-unneeded']}});return true;}}
 const monitor=createMonitorServer({startupGraceMs:0,receiver:`http://127.0.0.1:${receiver.address().port}`,sdkFactory:()=>({SmartSpectraSDK:FakeSDK,PixelFormat:{kRGBA:1},ProcessingStatus:{kError:9},decodeMetrics:x=>x,cardioMetrics:[],breathingMetrics:[],faceMetrics:[],ProcessingStatus:{kUninitialized:0,kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:9}})});
 await new Promise(r=>monitor.listen(0,'127.0.0.1',r));const url=`http://127.0.0.1:${monitor.address().port}`;
 const post=(path,token,data,headers={})=>fetch(url+path,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json',...headers},body:typeof data==='string'||Buffer.isBuffer(data)?data:JSON.stringify(data)});
 try{
  assert.equal((await post('/api/start','',{})).status,403);
  assert.equal((await post('/api/start','invalid',{})).status,403);
  assert.equal((await post('/api/start','alice',{}, {Origin:'https://other.example'})).status,403);
  const first=await (await post('/api/start','alice',{})).json();assert.equal(first.run_id,'run-1');
  assert.equal((await post('/api/stop','bob',{kind:'completed'})).status,409);
  assert.equal((await post('/api/frame','alice',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'12','X-Voice-On':'true','X-Background-On':'false'})).status,202);
  // A failed metric batch must not stop the SDK or reject the next frame.
  assert.equal((await post('/api/frame','alice',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'13','X-Voice-On':'true','X-Background-On':'false'})).status,202);
  // A processor error must not finalise the run: skip frames while resetting it.
  sdk.emit('error',122,'non-fatal processing notification',true);
  const stillRunning=await(await post('/api/frame','alice',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'13','X-Voice-On':'true','X-Background-On':'false'})).json();assert.equal(stillRunning.recovering,undefined);
  assert.equal(events.filter(e=>e.path==='/event'&&e.payload.kind==='failed').length,0);
  sdk.processingStatus=9;
  sdk.emit('error',123,'temporary failure',true);
  const skipped=await(await post('/api/frame','alice',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'14','X-Voice-On':'true','X-Background-On':'false'})).json();
  assert.equal(skipped.recovering,true);
  assert.equal(events.filter(e=>e.path==='/event'&&e.payload.kind==='failed').length,0);
  await new Promise(r=>setTimeout(r,1100));
  await post('/api/frame','alice',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'15','X-Voice-On':'true','X-Background-On':'false'});
  await new Promise(r=>setTimeout(r,20));
  assert.equal((await post('/api/frame','alice',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'16','X-Voice-On':'true','X-Background-On':'false'})).status,202);
  const paused=await(await post('/api/stop','alice',{kind:'paused',position:12})).json();assert.equal(paused.finalisation,'pending');
  const metric=events.find(e=>e.path==='/metrics');assert.equal(metric.payload.run_id,'run-1');assert.equal(metric.payload.position,12);assert.equal(metric.payload.levels.background_on,false);assert.equal(metric.payload.metrics.face.expression[0].scores[0].confidence,78);assert.equal(metric.payload.metrics.face.landmarks,undefined);assert.equal(metric.payload.metrics.cardio.waveform,undefined);
  const resumed=await(await post('/api/start','alice',{run_id:first.run_id})).json();assert.equal(resumed.run_id,'run-1');
  const finished=await(await post('/api/stop','alice',{kind:'completed',position:60})).json();
  assert.equal(finished.finalisation,'pending');assert.equal(finished.skipped_measurement_batches,2);
  const deadline=Date.now()+5000;
  while(events.filter(e=>e.path==='/event'&&e.payload.kind==='completed').length<2&&Date.now()<deadline)await new Promise(r=>setTimeout(r,50));
  assert.equal(events.filter(e=>e.path==='/event'&&e.payload.kind==='completed').length,2);
  assert(events.filter(e=>e.path==='/metrics').length>=3,'slow storage may coalesce duplicate metric packets');
  assert.equal(events.filter(e=>e.path==='/event'&&e.payload.kind==='paused').length,1,'resuming cancels the stale pending pause write');

  const replay=await(await post('/api/start','alice',{})).json();assert.equal(replay.run_id,'run-2');
  await post('/api/stop','alice',{kind:'interrupted',position:1});
  assert(events.some(e=>e.path==='/event'&&e.payload.kind==='completed'));
  const repeatedStop=await(await post('/api/stop','alice',{kind:'completed',position:60})).json();assert.equal(repeatedStop.finalisation,'already_stopped');
 }finally{await monitor.shutdown();await new Promise(r=>receiver.close(r));}
});

test('Default startup grace never stalls recovery from a terminal error for a minute',async()=>{
 let sdk,starts=0;const saved=[];
 const receiver=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;saved.push({path:req.url,data:JSON.parse(body)});res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/start'?{run_id:'recovery-run'}:{ok:true}));});
 await new Promise(r=>receiver.listen(0,'127.0.0.1',r));
 // Match the real SDK's single callback per event, rather than EventEmitter.
 class SDK{constructor(config){assert.deepEqual(config.requestedMetrics,[15,2,14]);this.callbacks=new Map();this.processingStatus=1;sdk=this;}on(name,fn){this.callbacks.set(name,fn);}useCustomInput(){}reset(){this.processingStatus=1;}start(){starts++;this.processingStatus=3;}async stopAsync(){this.processingStatus=1;}async destroy(){}sendFrame(){this.callbacks.get('metrics')({cardio:{pulseRate:[{value:72,timestamp:123}]},breathing:{rate:[{value:14,timestamp:123}]},face:{expression:[]}},123);return true;}}
 const monitor=createMonitorServer({receiver:`http://127.0.0.1:${receiver.address().port}`,sdkFactory:()=>({SmartSpectraSDK:SDK,MetricType:{PULSE_RATE:15,BREATHING_RATE:2,EXPRESSIONS:14},PixelFormat:{kRGBA:1},ProcessingStatus:{kUninitialized:0,kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:5},decodeMetrics:x=>x,cardioMetrics:[],breathingMetrics:[],faceMetrics:[]})});
 await new Promise(r=>monitor.listen(0,'127.0.0.1',r));
 const url=`http://127.0.0.1:${monitor.address().port}`;
 const post=(path,data,headers={})=>fetch(url+path,{method:'POST',headers:{Authorization:'Bearer alice',...headers},body:Buffer.isBuffer(data)?data:JSON.stringify(data)});
 const frame=()=>post('/api/frame',Buffer.alloc(16),{'X-Frame-Width':'2','X-Frame-Height':'2','X-Playback-Seconds':'9','X-Voice-On':'true'});
 try{
  await post('/api/start',{});assert.equal(starts,1);
  // Loading notices must never cause a reset.
  sdk.processingStatus=2;sdk.callbacks.get('error')(8,'loading notice',true);await frame();assert.equal(starts,1);
  // A terminal state already ended processing; recover during this short session.
  sdk.processingStatus=5;sdk.callbacks.get('error')(8,'processing failed',true);
  assert.equal((await(await frame()).json()).recovering,true);
  await new Promise(r=>setTimeout(r,1100));await frame();
  await new Promise(r=>setTimeout(r,10));assert.equal(starts,2);
  await frame();await post('/api/stop',{kind:'completed',position:60});
  const final=saved.find(e=>e.path==='/event'&&e.data.kind==='completed').data.detail;
  assert.equal(final.recovery_attempts,1);assert.equal(final.metrics_seen.pulse,true);assert.equal(final.metrics_seen.breathing,true);assert(final.frames_accepted>=1);
  const options=await fetch(url+'/api/frame',{method:'OPTIONS',headers:{Origin:'http://localhost:8501'}});assert.equal(options.headers.get('access-control-max-age'),'600');
 }finally{await monitor.shutdown();await new Promise(r=>receiver.close(r));}
});

test('Persistent frames preserve auth, RGBA bytes, playback metadata and pause lifecycle',async()=>{
 const {WebSocket}=require('ws');let frames=0,received=null;const saved=[];
 const receiver=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;saved.push({path:req.url,data:JSON.parse(body)});res.end(JSON.stringify(req.url==='/start'?{run_id:'socket-run'}:{ok:true}));});
 await new Promise(r=>receiver.listen(0,'127.0.0.1',r));
 class SDK extends EventEmitter{constructor(){super();this.processingStatus=1;}useCustomInput(){}start(){this.processingStatus=3;}reset(){this.processingStatus=1;}async stopAsync(){this.processingStatus=1;}async destroy(){}sendFrame(data,w,h,stride,format){frames++;received={data,w,h,stride,format};this.emit('metrics',{cardio:{pulseRate:[{timestamp:frames,value:72}]},breathing:{rate:[{timestamp:frames,value:14}]}});return true;}}
 const monitor=createMonitorServer({receiver:`http://127.0.0.1:${receiver.address().port}`,sdkFactory:()=>({SmartSpectraSDK:SDK,PixelFormat:{kRGBA:2},ProcessingStatus:{kUninitialized:0,kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:5},decodeMetrics:x=>x,breathingMetrics:[],cardioMetrics:[],faceMetrics:[]})});
 await new Promise(r=>monitor.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${monitor.address().port}`;
 const post=(route,data)=>fetch(base+route,{method:'POST',headers:{Authorization:'Bearer alice'},body:JSON.stringify(data)});
 const connect=async(token)=>{const ws=new WebSocket(base.replace('http:','ws:')+'/api/frames',{origin:'http://localhost:8501'});await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',reject);});const response=new Promise(resolve=>ws.once('message',d=>resolve(JSON.parse(d))));ws.send(JSON.stringify({type:'auth',token}));return {ws,response};};
 try{
  await post('/api/start',{});
  const bad=await connect('bob');await new Promise(r=>bad.ws.once('close',r));assert.equal(frames,0);
  const {ws,response}=await connect('alice');assert.equal((await response).type,'ready');
  for(let i=0;i<30;i++){
   const packet=Buffer.alloc(32,0);packet.writeUInt16LE(2,0);packet.writeUInt16LE(2,2);packet.writeDoubleLE(i/30,4);packet[12]=1;packet.fill(37,16);
   const ack=new Promise(resolve=>ws.once('message',d=>resolve(JSON.parse(d))));ws.send(packet);assert.equal((await ack).status,202);
  }
  assert.equal(frames,30);assert.deepEqual(received,{data:Buffer.alloc(16,37),w:2,h:2,stride:8,format:2});
  await post('/api/stop',{kind:'paused',position:1});
  const end=saved.find(e=>e.path==='/event'&&e.data.kind==='paused');assert.equal(end.data.detail.frames_accepted,30);
  assert(saved.some(e=>e.path==='/metrics'&&e.data.levels.voice_on===true&&e.data.levels.background_on===false));
  ws.close();
 }finally{await monitor.shutdown();await new Promise(r=>receiver.close(r));}
});

test('Native camera emits all three measurements without browser frames and stops when paused',async()=>{
 let running=false,timer,source;const saved=[];
 const receiver=createServer(async(req,res)=>{let body='';for await(const c of req)body+=c;saved.push({path:req.url,data:JSON.parse(body)});res.end(JSON.stringify(req.url==='/start'?{run_id:'native-run'}:{ok:true}));});
 await new Promise(r=>receiver.listen(0,'127.0.0.1',r));
 class SDK extends EventEmitter{
  constructor(){super();this.processingStatus=1;}
  useCamera(selection,options){source={selection,options};}
  useCustomInput(){throw new Error('No browser frames');}
  start(){running=true;this.processingStatus=3;let stamp=0;timer=setInterval(()=>{this.emit('frameSentThrough',true,++stamp);this.emit('metrics',{cardio:{pulseRate:[{value:72,timestamp:stamp,stable:true,confidence:90}]},breathing:{rate:[{value:14,timestamp:stamp,stable:true,confidence:90}]},face:{expression:[{timestamp:stamp,stable:true,scores:[{type:5,confidence:80}]}]}},stamp);},5);}
  reset(){this.processingStatus=1;}
  async stopAsync(){clearInterval(timer);running=false;this.processingStatus=1;}
  async destroy(){clearInterval(timer);}
 }
 const monitor=createMonitorServer({inputMode:'native',receiver:`http://127.0.0.1:${receiver.address().port}`,sdkFactory:()=>({SmartSpectraSDK:SDK,CameraSelection:{default:{kind:'default'}},ProcessingStatus:{kUninitialized:0,kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:5},decodeMetrics:x=>x,breathingMetrics:[],cardioMetrics:[],faceMetrics:[]})});
 await new Promise(r=>monitor.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${monitor.address().port}`;
 const post=(route,data,token='alice')=>fetch(base+route,{method:'POST',headers:{Authorization:'Bearer '+token},body:JSON.stringify(data)});
 try{
  assert.equal(running,false);const start=await(await post('/api/start',{})).json();assert.equal(start.capture_mode,'native');assert.equal(start.frame_socket,null);assert.equal(running,true);
  assert.deepEqual(source.options,{width:640,height:480,fps:30});
  assert.equal((await post('/api/ping',{position:20,levels:{voice_on:true}},'bob')).status,409);
  assert.equal((await post('/api/ping',{position:20,levels:{voice_on:true}})).status,200);
  const deadline=Date.now()+1000;while(!saved.some(e=>e.path==='/metrics')&&Date.now()<deadline)await new Promise(r=>setTimeout(r,10));
  await post('/api/stop',{kind:'paused',position:25});assert.equal(running,false);
  const end=saved.find(e=>e.path==='/event'&&e.data.kind==='paused').data.detail;
  assert.equal(end.capture_mode,'native');assert.deepEqual(end.metrics_seen,{pulse:true,breathing:true,expressions:true});assert(end.frames_accepted>0);
  const measurements=saved.find(e=>e.path==='/metrics').data;assert.equal(measurements.position,20);assert.equal(measurements.metrics.cardio.pulseRate[0].value,72);assert.equal(measurements.metrics.breathing.rate[0].value,14);
 }finally{await monitor.shutdown();await new Promise(r=>receiver.close(r));}
});

test('Pause saves final accumulated vitals even if continuous metrics never arrived',async()=>{
 const saved=[];let sdk;
 const receiver=createServer(async(req,res)=>{let data='';for await(const part of req)data+=part;saved.push({path:req.url,data:JSON.parse(data)});res.end(JSON.stringify(req.url==='/start'?{run_id:'final-run'}:{ok:true}));});
 await new Promise(r=>receiver.listen(0,'127.0.0.1',r));
 class SDK extends EventEmitter {
  constructor(options){super();assert.equal(options.enableAccumulatedOutput,true);this.processingStatus=1;sdk=this;}
  useCustomInput(){}
  start(){this.processingStatus=3;}
  async stopAsync(){
   this.processingStatus=1;
   setImmediate(()=>this.emit('accumulatedMetrics',{
    cardio:{pulseRate:[{timestamp:1,value:72}]},
    breathing:{rate:[{timestamp:1,value:14}]},
    face:{expression:[{timestamp:1,scores:[{type:5,confidence:80}]}]}
   }));
  }
  reset(){this.processingStatus=1;}
  async destroy(){}
 }
 const monitor=createMonitorServer({receiver:`http://127.0.0.1:${receiver.address().port}`,sdkFactory:()=>({SmartSpectraSDK:SDK,ProcessingStatus:{kIdle:1,kRunning:3,kStarting:2,kStopping:4,kError:5},decodeMetrics:x=>x,breathingMetrics:[],cardioMetrics:[],faceMetrics:[]})});
 await new Promise(r=>monitor.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${monitor.address().port}`;
 const post=(path,body,token='alice')=>fetch(base+path,{method:'POST',headers:{Authorization:'Bearer '+token},body:JSON.stringify(body)});
 try{
  await post('/api/start',{});sdk.emit('validationStatus',7,1,'Place more of the chest in view.');
  const status=await(await post('/api/status',{})).json();assert.equal(status.validation.code,7);
  assert.equal((await post('/api/status',{},'bob')).status,409);
  await post('/api/stop',{kind:'paused',position:25});
  const metrics=saved.find(e=>e.path==='/metrics');assert.equal(metrics.data.metrics.cardio.pulseRate[0].value,72);assert.equal(metrics.data.metrics.breathing.rate[0].value,14);
  const end=saved.find(e=>e.path==='/event'&&e.data.kind==='paused');assert.equal(end.data.detail.metrics_seen.pulse,true);
  assert(saved.indexOf(metrics)<saved.indexOf(end));
 }finally{await monitor.shutdown();await new Promise(r=>receiver.close(r));}
});

test('Slow camera initialization does not immediately trip the no-heartbeat watchdog',async()=>{
 let clock=0;
 const receiver=createServer(async(req,res)=>{for await(const ignored of req){}res.end(JSON.stringify(req.url==='/start'?{run_id:'slow-start'}:{ok:true}));});
 await new Promise(r=>receiver.listen(0,'127.0.0.1',r));
 class SDK extends EventEmitter{constructor(){super();this.processingStatus=1;}useCamera(){}start(){clock=15000;this.processingStatus=3;}async stopAsync(){this.processingStatus=1;}reset(){this.processingStatus=1;}async destroy(){}}
 const monitor=createMonitorServer({inputMode:'native',now:()=>clock,receiver:`http://127.0.0.1:${receiver.address().port}`,sdkFactory:()=>({SmartSpectraSDK:SDK,CameraSelection:{default:{kind:'default'}},ProcessingStatus:{kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:5},breathingMetrics:[],cardioMetrics:[],faceMetrics:[]})});
 await new Promise(r=>monitor.listen(0,'127.0.0.1',r));const base=`http://127.0.0.1:${monitor.address().port}`;
 const post=(path,data)=>fetch(base+path,{method:'POST',headers:{Authorization:'Bearer alice'},body:JSON.stringify(data)});
 try{
  assert.equal((await post('/api/start',{})).status,200);
  await new Promise(r=>setTimeout(r,2100));
  const status=await post('/api/status',{});assert.equal(status.status,200);assert.equal((await status.json()).running,true);
  await post('/api/stop',{kind:'paused',position:1});
 }finally{await monitor.shutdown();await new Promise(r=>receiver.close(r));}
});
