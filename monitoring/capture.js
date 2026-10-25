// Account-scoped capture: missing readings do not control the playback lifecycle.
let monitorStream=null,monitorRun=null,monitorTimer=null,monitorEpoch=0,frameBusy=false,skippedFrames=0,activeFrameRequest=null,monitorQueue=Promise.resolve();
let serviceActive=false,serviceBusy=false,nextServiceAttempt=0;
let monitorSocket=null,socketFrame=null;
const nativeCapture=monitorConfig.capture_mode==='native';
let monitorErrorText='';
function showMonitorError(text){monitorErrorText=text;document.getElementById('error').textContent=text;}
function clearMonitorError(){const element=document.getElementById('error');if(monitorErrorText&&element.textContent===monitorErrorText)element.textContent='';monitorErrorText='';}
const monitorVideo=document.createElement('video');monitorVideo.muted=true;monitorVideo.playsInline=true;
monitorVideo.style.cssText='position:fixed;left:-10000px;width:1px;height:1px;';document.body.appendChild(monitorVideo);
const monitorCanvas=document.createElement('canvas');
function levels(){return {voice_on:Boolean(voice&&!voice.muted),background_on:Boolean(background&&!background.muted)};}
async function monitorRequest(path,body,headers={}){
 const timeout=path==='/api/start'?45000:path==='/api/stop'?20000:5000;
 const response=await fetch(monitorConfig.url+path,{method:'POST',headers:{Authorization:'Bearer '+monitorConfig.token,...headers},body,keepalive:path==='/api/stop',signal:AbortSignal.timeout(timeout)});
 const result=await response.json();if(!response.ok)throw Object.assign(new Error(result.error||'Measurement service unavailable.'),{status:response.status});return result;
}
function closeFrameSocket(){
 if(socketFrame){socketFrame({ok:false,status:503});socketFrame=null;}
 if(monitorSocket){const socket=monitorSocket;monitorSocket=null;socket.close();}
}
async function openFrameSocket(path){
 if(!path||typeof WebSocket==='undefined')return;
 try{await new Promise(resolve=>{
  const url=new URL(path,monitorConfig.url);url.protocol=url.protocol==='https:'?'wss:':'ws:';
  const socket=new WebSocket(url.href);monitorSocket=socket;
  let done=false;
  const finish=()=>{if(done)return;done=true;clearTimeout(timeout);resolve();};
  const timeout=setTimeout(()=>{socket.close();finish();},1500);
  socket.onopen=()=>socket.send(JSON.stringify({type:'auth',token:monitorConfig.token}));
  socket.onmessage=event=>{let data;try{data=JSON.parse(event.data);}catch{return;}
   if(data.type==='ready'){socket.authorised=true;finish();}
   else if(data.type==='frame'&&socketFrame){const complete=socketFrame;socketFrame=null;complete({ok:data.status>=200&&data.status<300,status:data.status});}
  };
  socket.onerror=()=>{socket.close();finish();};
  socket.onclose=()=>{if(monitorSocket===socket){monitorSocket=null;if(socketFrame){const complete=socketFrame;socketFrame=null;complete({ok:false,status:503});}}finish();};
 });}catch{closeFrameSocket();}
}
function sendSocketFrame(pixels,width,height){
 const socket=monitorSocket;
 const packet=new ArrayBuffer(16+pixels.byteLength),view=new DataView(packet);
 view.setUint16(0,width,true);view.setUint16(2,height,true);view.setFloat64(4,master.currentTime,true);
 const current=levels();view.setUint8(12,(current.voice_on?1:0)|(current.background_on?2:0));
 new Uint8Array(packet,16).set(pixels);
 return new Promise(resolve=>{
  const timeout=setTimeout(()=>{closeFrameSocket();resolve({ok:false,status:503});},2000);
  socketFrame=response=>{clearTimeout(timeout);resolve(response);};
  try{socket.send(packet);}catch{closeFrameSocket();}
 });
}
function closeCamera(){closeFrameSocket();clearInterval(monitorTimer);monitorTimer=null;if(monitorStream)monitorStream.getTracks().forEach(t=>t.stop());monitorStream=null;monitorVideo.srcObject=null;}
function authorisationEnded(){closeCamera();serviceActive=false;document.getElementById('error').textContent='Measurement permission expired. Sign in again to continue saving measurements. Audio still plays.';}
async function ensureService(){
 if(serviceActive||serviceBusy||!playing||(!nativeCapture&&!monitorStream)||Date.now()<nextServiceAttempt)return;
 serviceBusy=true;const epoch=monitorEpoch;
 try{
  const response=await monitorRequest('/api/start',JSON.stringify({run_id:monitorRun,position:master.currentTime,levels:levels()}),{'Content-Type':'application/json'});
  monitorRun=response.run_id;serviceActive=true;clearMonitorError();
  if(!nativeCapture)await openFrameSocket(response.frame_socket);
  if(epoch!==monitorEpoch||!playing){serviceActive=false;await monitorRequest('/api/stop',JSON.stringify({kind:'paused',position:Math.min(master.currentTime,duration)}),{'Content-Type':'application/json'});}
 }catch(error){serviceActive=false;nextServiceAttempt=Date.now()+1000;if(error.status===403)authorisationEnded();else{skippedFrames++;if(nativeCapture)showMonitorError((error.name==='TimeoutError'||error.name==='AbortError'?'Camera startup is taking longer than expected. Retrying automatically.':error.message)+' Audio still plays.');}}
 finally{serviceBusy=false;}
}
async function captureFrame(){
 if(frameBusy||!playing||(!nativeCapture&&!monitorStream))return;
 if(!serviceActive){await ensureService();return;}
 if(nativeCapture){
  frameBusy=true;
  try{
   activeFrameRequest=monitorRequest('/api/ping',JSON.stringify({position:master.currentTime,levels:levels()}),{'Content-Type':'application/json'});
   await activeFrameRequest;clearMonitorError();
  }catch(error){if(error.status===403)authorisationEnded();else if(error.status===409){serviceActive=false;nextServiceAttempt=Date.now()+1000;}}
  finally{frameBusy=false;activeFrameRequest=null;}
  return;
 }
 if(monitorVideo.readyState<2)return;
 frameBusy=true;const captureStarted=Date.now();
 try{
  const scale=Math.min(1,640/monitorVideo.videoWidth,480/monitorVideo.videoHeight);
  const width=Math.max(1,Math.round(monitorVideo.videoWidth*scale)),height=Math.max(1,Math.round(monitorVideo.videoHeight*scale));
  monitorCanvas.width=width;monitorCanvas.height=height;
  const ctx=monitorCanvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(monitorVideo,0,0,width,height);
  const pixels=ctx.getImageData(0,0,width,height).data;
  activeFrameRequest=monitorSocket?.readyState===1&&monitorSocket.authorised
   ?sendSocketFrame(pixels,width,height)
   :fetch(monitorConfig.url+'/api/frame',{method:'POST',headers:{Authorization:'Bearer '+monitorConfig.token,'Content-Type':'application/octet-stream','X-Frame-Width':String(width),'X-Frame-Height':String(height),'X-Playback-Seconds':String(master.currentTime),'X-Voice-On':String(levels().voice_on),'X-Background-On':String(levels().background_on)},body:pixels,signal:AbortSignal.timeout(2000)});
  const response=await activeFrameRequest;
  if(!response.ok){skippedFrames++;if(response.status===403)authorisationEnded();else if(response.status===409){serviceActive=false;nextServiceAttempt=Date.now()+1000;}}
 }catch{skippedFrames++;}
 finally{
  frameBusy=false;activeFrameRequest=null;
  // A slow upload must not also lose the next 33ms timer slot. Keep one
  // request in flight and catch up using a fresh frame, without a backlog.
  if(playing&&monitorStream&&Date.now()-captureStarted>=34)setTimeout(captureFrame,0);
 }
}
function monitorStart(){
 const epoch=++monitorEpoch;
 monitorQueue=monitorQueue.then(async()=>{
  if(!playing||epoch!==monitorEpoch)return;
  try{
   if(nativeCapture){
    serviceActive=false;nextServiceAttempt=0;
    monitorTimer=setInterval(captureFrame,1000);
    await ensureService();
    return;
   }
   if(window.isSecureContext===false||!navigator.mediaDevices?.getUserMedia)throw Object.assign(new Error('Camera needs localhost or HTTPS.'),{name:'SecurityError'});
   const stream=await navigator.mediaDevices.getUserMedia({video:{width:{ideal:640},height:{ideal:480},frameRate:{ideal:30}},audio:false});
   if(!playing||epoch!==monitorEpoch){stream.getTracks().forEach(t=>t.stop());return;}
   monitorStream=stream;monitorVideo.srcObject=stream;await monitorVideo.play();
   serviceActive=false;nextServiceAttempt=0;
   // Start the retry loop even when the first processing request fails.
   monitorTimer=setInterval(captureFrame,1000 / 30);
   await ensureService();
  }catch(error){closeCamera();const reason=error.name==='NotAllowedError'?'Camera access was denied. Allow camera access, then pause and play again.':error.name==='NotFoundError'?'No camera was found. Connect a camera, then pause and play again.':error.name==='SecurityError'?'Open the app at localhost or over HTTPS to allow camera access.':'Camera unavailable. Check camera permissions, then pause and play again.';document.getElementById('error').textContent=reason+' Audio still plays.';}
 });
}
function monitorStop(kind='paused'){
 ++monitorEpoch;closeCamera();serviceActive=false;
 monitorQueue=monitorQueue.then(async()=>{
  if(activeFrameRequest)try{await activeFrameRequest;}catch{}
  if(!monitorRun)return;
  try{await monitorRequest('/api/stop',JSON.stringify({kind,position:Math.min(master.currentTime,duration),skipped_frames:skippedFrames}),{'Content-Type':'application/json'});}
  catch{if(kind!=='interrupted')document.getElementById('error').textContent='Run completion could not be confirmed. Previously saved readings remain in your profile.';}
  if(kind!=='paused'){monitorRun=null;skippedFrames=0;}
 });
}
window.addEventListener('pagehide',()=>{
 closeCamera();
 if(monitorRun)void monitorRequest('/api/stop',JSON.stringify({kind:'interrupted',position:Math.min(master.currentTime,duration)}),{'Content-Type':'application/json'}).catch(()=>{});
});
