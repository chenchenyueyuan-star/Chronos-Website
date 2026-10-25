'use strict';
// Adapted from the user's SmartSpectra sample. Frames stay in memory only.
const {createServer} = require('node:http');
const {Readable} = require('node:stream');
const {WebSocketServer} = require('ws');
const {createPresageCore}=require('./presage_core');
const {withObservers}=require('./sdk_events');
const {mergeMetrics}=require('./metric_batches');

function createMonitorServer({sdkFactory, receiver='http://127.0.0.1:8766', allowedOrigins=['http://localhost:8501','http://127.0.0.1:8501'],startupGraceMs=60000,inputMode='browser',now=()=>Date.now()}={}) {
  let sdk, core, statuses, active=null, busy=false, pending=null, delivery=null;
  const origins=new Set(allowedOrigins);
  const finalisations=new Map(),runDiagnostics=new Map();
  async function saveFinal(item){try{await send('/event',item.token,item.payload);return true;}catch{return false;}}
  async function send(path, token, body) {
    const response=await fetch(receiver+path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(body),signal:AbortSignal.timeout(5000)});
    if(!response.ok) throw Object.assign(new Error('Measurement storage unavailable or session expired.'),{statusCode:response.status});
    return response.json();
  }
  function init() {
    if(sdk)return;
    const api=sdkFactory ? sdkFactory() : require('@smartspectra/node-sdk');
    statuses=api.ProcessingStatus;
    // Avoid requesting the unused pressure/HRV/micromotion models. A failure in
    // one optional model can terminate the shared graph before vitals warm up.
    const required=api.MetricType
      ? [api.MetricType.PULSE_RATE,api.MetricType.BREATHING_RATE,api.MetricType.EXPRESSIONS]
      : null;
    core=createPresageCore({...api,SmartSpectraSDK:withObservers(api.SmartSpectraSDK)},process.env.PRESAGE_API_KEY,async(payload)=>{
      if(!active || active.finalising)return;
      for(const [name,samples] of Object.entries({pulse:payload.metrics.cardio?.pulseRate,breathing:payload.metrics.breathing?.rate,expressions:payload.metrics.face?.expression})){
        if(Array.isArray(samples)&&samples.length)active.metricsSeen[name]=true;
      }
      pending={owner:active,metrics:mergeMetrics(pending?.owner===active?pending.metrics:null,payload.metrics,payload.timestampUs),timestampUs:payload.timestampUs,position:active.position,levels:active.levels};
      await pump();
    },required,inputMode);
    sdk=core.sdk;
    if(inputMode==='native')sdk.on('frameSentThrough',sent=>{
      if(!active||active.stopping)return;
      active.framesReceived++;
      if(sent){active.framesAccepted++;active.attemptFrames++;active.frameLastAt=now();if(!active.frameFirstAt)active.frameFirstAt=active.frameLastAt;}
    });
    sdk.on('validationStatus',(code,timestampUs,hint)=>{
      if(active){
        active.validation={code,hint:typeof hint==='string'?hint.slice(0,200):''};
        if(active.validationHistory.at(-1)?.code!==code){
          active.validationHistory.push({...active.validation,position:active.position});
          if(active.validationHistory.length>50)active.validationHistory.shift();
          if(code!==0&&now()-(active.lastWarningAt||0)>2000){active.lastWarningAt=now();console.warn('Presage camera feedback: '+active.validation.hint);}
        }
      }
    });
    sdk.on('error',(code,message,retryable)=>{
      if(!active||active.stopping)return;
      let safe=String(message||'SDK processing interruption.');
      if(process.env.PRESAGE_API_KEY)safe=safe.replaceAll(process.env.PRESAGE_API_KEY,'[redacted]');
      active.sdkError={code,message:safe.slice(0,600),retryable:Boolean(retryable),processingStatus:sdk.processingStatus,accepted_fps:active.frameLastAt>active.frameFirstAt?Number(((active.attemptFrames-1)*1000/(active.frameLastAt-active.frameFirstAt)).toFixed(1)):null,validation:active.validation};
      active.error=`Presage SDK error ${code}: ${safe.slice(0,600)}`;
      // Notifications are not necessarily terminal. Never reset a live/loading graph.
      // kError means processing has already stopped: a startup grace period here
      // would discard the rest of a short session rather than protect model loading.
      if(sdk.processingStatus===statuses.kError){
        active.needsRecovery=true;active.recoveryAllowed=retryable!==false;
        active.recoverAt=now()+Math.min(60000,1000*2**active.recoveryAttempts);
        active.skippedBatches++;
      }
    });
  }
  async function recoverProcessor(owner){
    if(owner.recoveryBusy||!owner.needsRecovery||owner.recoveryAllowed===false||now()<owner.recoverAt)return false;
    owner.recoveryAttempts++;
    owner.recoveryBusy=true;
    try{
      await core.stopMeasurement();
      if(active!==owner||owner.stopping)return false;
      owner.needsRecovery=false;owner.lastStartAt=now();owner.frameFirstAt=0;owner.frameLastAt=0;owner.attemptFrames=0;core.startMeasurement();
      return !owner.needsRecovery;
    }catch{owner.needsRecovery=true;owner.skippedBatches++;return false;}
    finally{owner.recoverAt=now()+Math.min(60000,1000*2**owner.recoveryAttempts);owner.recoveryBusy=false;}
  }
  async function pump() {
    if(delivery)return delivery;
    delivery=(async()=>{while(pending){const item=pending;pending=null;try {await send('/metrics',item.owner.token,{type:'metrics',timestampUs:item.timestampUs,run_id:item.owner.runId,metrics:item.metrics,position:item.position,levels:item.levels});}catch(error){item.owner.skippedBatches++;if(error.statusCode===403){item.owner.error='Measurement authorisation expired.';void stop('failed').catch(()=>{});}}}})().finally(()=>{delivery=null;});
    return delivery;
  }
  async function stop(kind='paused') {
    if(!active)return;
    const owner=active;
    if(owner.stopPromise)return owner.stopPromise;
    owner.stopping=true;
    owner.stopPromise=(async()=>{
      try {let finalKind=kind;try{if(core){await core.stopMeasurement();await new Promise(setImmediate);}}catch{owner.error='Processor cleanup incomplete; some readings may be missing.';}owner.finalising=true;if(pending)await pump();if(delivery)await delivery;
        const payload={run_id:owner.runId,kind:finalKind,position:owner.position,detail:{validation:owner.validation,error:owner.error,sdk_error:owner.sdkError||null,capture_mode:inputMode,validation_history:owner.validationHistory,metrics_seen:owner.metricsSeen,frames_received:owner.framesReceived,frames_accepted:owner.framesAccepted,recovery_attempts:owner.recoveryAttempts,skipped_measurement_batches:owner.skippedBatches,skipped_playback_events:owner.skippedEvents,skipped_camera_frames:owner.skippedFrames}};
        const saved=await saveFinal({token:owner.token,payload});
        runDiagnostics.set(owner.runId,{skippedBatches:owner.skippedBatches,skippedEvents:owner.skippedEvents,skippedFrames:owner.skippedFrames});
        if(finalKind!=='paused')runDiagnostics.delete(owner.runId);
        if(!saved){if(finalisations.size>=32)finalisations.delete(finalisations.keys().next().value);finalisations.set(owner.runId,{token:owner.token,payload,attempts:0,inFlight:null});}
        if(runDiagnostics.size>32)runDiagnostics.delete(runDiagnostics.keys().next().value);
        return {ok:true,finalisation:saved?'saved':'pending',skipped_measurement_batches:owner.skippedBatches};}
      finally {if(active===owner)active=null;}
    })();
    return owner.stopPromise;
  }
  function reply(res,status,data){const body=JSON.stringify(data);res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(body);}
  async function body(req,max=1280*720*4){const parts=[];let size=0;for await(const part of req){size+=part.length;if(size>max)throw new Error('Payload too large.');parts.push(part);}return Buffer.concat(parts);}
  async function handle(req,res){
    const origin=req.headers.origin;
    if(origin && !origins.has(origin)){reply(res,403,{error:'Origin not permitted.'});return;}
    if(origin){res.setHeader('Access-Control-Allow-Origin',origin);res.setHeader('Vary','Origin');}
    if(req.method==='OPTIONS'){res.setHeader('Access-Control-Allow-Methods','GET, POST, OPTIONS');res.setHeader('Access-Control-Allow-Headers','Authorization, Content-Type, X-Frame-Width, X-Frame-Height, X-Playback-Seconds, X-Voice-On, X-Background-On');res.setHeader('Access-Control-Max-Age','600');res.writeHead(204);res.end();return;}
    if(req.url==='/health'&&req.method==='GET'){reply(res,200,{ok:true,version:11,capture_mode:inputMode,configured:Boolean(process.env.PRESAGE_API_KEY)});return;}
    const match=(req.headers.authorization||'').match(/^Bearer\s+(\S+)$/);const token=match?match[1]:'';
    if(!token || token.length>200){reply(res,403,{error:'A signed-in measurement session is required.'});return;}
    if(active && active.token!==token){reply(res,409,{error:'The camera monitor is in use by another session.'});return;}
    if(req.method!=='POST'){reply(res,405,{error:'POST required.'});return;}
    if(req.url==='/api/start'){
      if(active&&!active.stopping&&active.token===token){reply(res,200,{run_id:active.runId,frame_socket:inputMode==='browser'?'/api/frames':null,capture_mode:inputMode});return;}
      if(busy||active){reply(res,409,{error:'The previous measurement is still active.'});return;}
      if(!process.env.PRESAGE_API_KEY && !sdkFactory){reply(res,503,{error:'The camera measurement service is not configured.'});return;}
      busy=true;
      try {
        const data=JSON.parse((await body(req,10000)).toString());
        // The Python receiver binds the ticket to a verified account, not a browser-supplied username.
        const waiting=data.run_id?finalisations.get(data.run_id):null;
        if(waiting&&waiting.token===token&&waiting.payload.kind==='paused'){finalisations.delete(data.run_id);if(waiting.inFlight)await waiting.inFlight;}
        const run=await send('/start',token,{run_id:data.run_id||null});
        active={token,runId:run.run_id,position:Number(data.position)||0,levels:data.levels||{},lastFrame:now(),validation:null,error:null,skippedBatches:0,skippedEvents:0,skippedFrames:0,validationHistory:[],frameFirstAt:0,frameLastAt:0,attemptFrames:0,framesReceived:0,framesAccepted:0,metricsSeen:{pulse:false,breathing:false,expressions:false},needsRecovery:false,recoveryBusy:false,recoverAt:0,lastStartAt:now(),recoveryAttempts:0,recoveryAllowed:true};
        Object.assign(active,runDiagnostics.get(run.run_id)||{});
        init();
        try{core.startMeasurement();}catch(error){if(inputMode==='native')throw error;active.needsRecovery=true;active.recoverAt=now()+(sdk.processingStatus===statuses.kError?1000:startupGraceMs);active.skippedBatches++;}
        // Native camera/model setup may block for several seconds. The idle
        // watchdog starts when capture is ready, not before initialization.
        active.lastFrame=now();
        reply(res,200,{run_id:active.runId,frame_socket:inputMode==='browser'?'/api/frames':null,capture_mode:inputMode});
      } catch (error) {
        if(active)try{await stop('failed');}catch{active=null;}
        reply(res,error.statusCode===403?403:503,{error:inputMode==='native'&&error.code===7?'Could not open the Mac camera. Allow camera access for Terminal or your launching app in System Settings > Privacy & Security > Camera, then pause and play again.':'Could not start camera measurements. Check the Presage key and local receiver.'});
      } finally {busy=false;}
      return;
    }
    if(!active){if(req.url==='/api/stop'){reply(res,200,{ok:true,finalisation:'already_stopped'});return;}reply(res,409,{error:'No active measurement.'});return;}
    if(req.url==='/api/stop'){
      const data=JSON.parse((await body(req,10000)).toString());
      const kind=['paused','completed','interrupted','failed'].includes(data.kind)?data.kind:'paused';
      active.position=Number.isFinite(data.position)?data.position:active.position;
      active.skippedFrames=Number.isInteger(data.skipped_frames)?Math.max(0,data.skipped_frames):active.skippedFrames;
      const result=await stop(kind);reply(res,200,result);return;
    }
    if(req.url==='/api/status'){
      const owner=active;
      const fps=owner.frameLastAt>owner.frameFirstAt?Number(((owner.attemptFrames-1)*1000/(owner.frameLastAt-owner.frameFirstAt)).toFixed(1)):null;
      reply(res,200,{running:!owner.stopping,validation:owner.validation,accepted_fps:fps,metrics_seen:owner.metricsSeen,error:owner.error});return;
    }
    if(req.url==='/api/ping'){
      const data=JSON.parse((await body(req,10000)).toString());
      if(!Number.isFinite(data.position)||data.position<0){reply(res,400,{error:'Invalid playback position.'});return;}
      const owner=active;owner.position=data.position;owner.lastFrame=now();
      const nextLevels={voice_on:data.levels?.voice_on===true,background_on:data.levels?.background_on===true};
      if(JSON.stringify(owner.levels)!==JSON.stringify(nextLevels))void send('/event',token,{run_id:owner.runId,kind:'levels',position:owner.position,detail:nextLevels}).catch(()=>{owner.skippedEvents++;});
      owner.levels=nextLevels;
      if(!owner.lastHeartbeat||now()-owner.lastHeartbeat>5000){owner.lastHeartbeat=now();void send('/heartbeat',token,{run_id:owner.runId}).catch(()=>{});}
      if(owner.needsRecovery)void recoverProcessor(owner);
      reply(res,200,{ok:true});return;
    }
    if(req.url==='/api/frame'){
      const width=Number(req.headers['x-frame-width']),height=Number(req.headers['x-frame-height']);
      if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>1280||height>720){reply(res,400,{error:'Invalid frame dimensions.'});return;}
      const frame=await body(req,1280*720*4);
      if(frame.length!==width*height*4){reply(res,400,{error:'Invalid RGBA frame.'});return;}
      const owner=active;if(!owner||owner.stopping){reply(res,409,{error:'Measurement has stopped.'});return;}
      const position=Number(req.headers['x-playback-seconds']);
      if(!Number.isFinite(position)||position<0){reply(res,400,{error:'Invalid playback position.'});return;}
      const levels={voice_on:req.headers['x-voice-on']==='true',background_on:req.headers['x-background-on']==='true'};
      if(JSON.stringify(owner.levels)!==JSON.stringify(levels)){try{await send('/event',token,{run_id:owner.runId,kind:'levels',position,detail:levels});}catch(error){owner.skippedEvents++;if(error.statusCode===403){await stop('failed');reply(res,403,{error:'Session authorisation expired.'});return;}}}
      owner.position=position;owner.levels=levels;owner.lastFrame=now();owner.framesReceived++;
      if(!owner.lastHeartbeat||now()-owner.lastHeartbeat>5000){owner.lastHeartbeat=now();void send('/heartbeat',token,{run_id:owner.runId}).catch(()=>{});}
      if(owner.needsRecovery){void recoverProcessor(owner);reply(res,202,{ok:true,skipped:true,recovering:true});return;}
      try{
        const accepted=core.sendFrame(frame,width,height,Number(process.hrtime.bigint()/1000n));
        if(!accepted)owner.skippedFrames++;else{owner.framesAccepted++;owner.attemptFrames++;owner.frameLastAt=now();if(!owner.frameFirstAt)owner.frameFirstAt=owner.frameLastAt;}
        reply(res,accepted?202:429,{ok:accepted});
      }catch{if(sdk.processingStatus===statuses.kError){owner.needsRecovery=true;owner.recoverAt=now()+1000;}owner.skippedFrames++;reply(res,202,{ok:true,skipped:true});}
      return;
    }
    reply(res,404,{error:'Not found.'});
  }
  const server=createServer((req,res)=>{void handle(req,res).catch(()=>{if(!res.headersSent)reply(res,500,{error:'Measurement service error.'});else res.end();});});
  // The browser sends RGBA over a single ordered connection. Keep the same
  // authenticated frame handler for HTTP fallback and WebSocket delivery.
  const frameSockets=new WebSocketServer({noServer:true,maxPayload:1280*720*4+16,perMessageDeflate:false});
  server.on('upgrade',(req,socket,head)=>{
    if(req.url!=='/api/frames'||(req.headers.origin&&!origins.has(req.headers.origin))){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;}
    frameSockets.handleUpgrade(req,socket,head,ws=>frameSockets.emit('connection',ws));
  });
  frameSockets.on('connection',ws=>{
    let token=null,inFlight=false;
    const authenticationDeadline=setTimeout(()=>ws.close(1008,'Authentication required'),3000);
    ws.on('error',()=>{});
    ws.on('close',()=>clearTimeout(authenticationDeadline));
    ws.on('message',(data,isBinary)=>{
      if(!token){
        try{const hello=JSON.parse(data.toString());if(isBinary||hello.type!=='auth'||!active||active.stopping||hello.token!==active.token)throw new Error();token=hello.token;clearTimeout(authenticationDeadline);ws.send(JSON.stringify({type:'ready'}));}
        catch{ws.close(1008,'Session authorisation failed');}return;
      }
      if(!isBinary||data.length<16||inFlight){ws.close(1008,'One valid frame at a time required');return;}
      if(!active||active.token!==token||active.stopping){ws.send(JSON.stringify({type:'frame',status:409}));return;}
      inFlight=true;
      const req=Readable.from([data.subarray(16)]);
      req.method='POST';req.url='/api/frame';
      req.headers={authorization:'Bearer '+token,'x-frame-width':String(data.readUInt16LE(0)),'x-frame-height':String(data.readUInt16LE(2)),'x-playback-seconds':String(data.readDoubleLE(4)),'x-voice-on':String(Boolean(data[12]&1)),'x-background-on':String(Boolean(data[12]&2))};
      let status=500,ended=false;
      const res={headersSent:false,setHeader(){},writeHead(code){status=code;this.headersSent=true;},end(body){if(ended)return;ended=true;inFlight=false;let result={};try{result=JSON.parse(String(body));}catch{}if(ws.readyState===1)ws.send(JSON.stringify({...result,type:'frame',status}));}};
      void handle(req,res).catch(()=>res.end());
    });
  });
  let retryBusy=false;
  const retryFinals=setInterval(async()=>{if(retryBusy)return;retryBusy=true;try{for(const [id,item] of finalisations){item.attempts++;item.inFlight=saveFinal(item);const saved=await item.inFlight;item.inFlight=null;if(finalisations.get(id)===item&&(saved||item.attempts>=3))finalisations.delete(id);}}finally{retryBusy=false;}},2000);retryFinals.unref();
  const watchdog=setInterval(()=>{if(active&&!active.stopping&&now()-active.lastFrame>10000)void stop('interrupted').catch(()=>{});},2000);watchdog.unref();
  server.shutdown=async()=>{for(const ws of frameSockets.clients)ws.terminate();frameSockets.close();clearInterval(watchdog);clearInterval(retryFinals);try{await stop('interrupted');}finally{if(sdk)await sdk.destroy();server.close();}};
  return server;
}
module.exports={createMonitorServer};
if(require.main===module){
  const server=createMonitorServer({inputMode:process.env.CHRONOS_CAMERA_INPUT==='native'?'native':'browser',receiver:process.env.MEASUREMENT_RECEIVER_URL||'http://127.0.0.1:8766',allowedOrigins:(process.env.CHRONOS_APP_ORIGINS||'http://localhost:8501,http://127.0.0.1:8501').split(',')});
  server.on('error',()=>{console.error('Camera monitor could not bind its local port.');process.exitCode=1;});
  server.listen(Number(process.env.MONITOR_PORT)||3001,'127.0.0.1',()=>console.log(process.env.CHRONOS_CAMERA_INPUT==='native'?'Local camera monitor ready: Presage camera capture (30 fps target).':'Local camera monitor ready: browser frame capture.'));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>void server.shutdown());
}
