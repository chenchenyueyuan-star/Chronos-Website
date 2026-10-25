'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

test('Camera follows play/pause/resume/replay without showing a camera dashboard',async()=>{
 let closes=0,next=0;const calls=[];
 const video={muted:false,playsInline:false,style:{},readyState:2,videoWidth:640,videoHeight:480,play:async()=>{}};
 const context={AbortSignal,monitorConfig:{url:'http://monitor',token:'ticket'},playing:true,master:{currentTime:0},duration:60,voice:{muted:false},background:{muted:true},
 document:{createElement:tag=>tag==='video'?video:{getContext:()=>({drawImage(){},getImageData:()=>({data:new Uint8Array(640*480*4)})})},body:{appendChild(){}},getElementById:()=>({textContent:''})},
 navigator:{mediaDevices:{getUserMedia:async config=>{assert.equal(config.audio,false);return {getTracks:()=>[{stop:()=>{closes++;}}]};}}},
 window:{addEventListener(){}},setInterval:()=>1,clearInterval(){},
 fetch:async(url,opts)=>{calls.push({url,opts});if(url.endsWith('/api/frame')&&calls.filter(c=>c.url.endsWith('/api/frame')).length===1)return {ok:false,status:503};return {ok:true,status:200,json:async()=>({run_id:opts.headers['Content-Type']==='application/json'&&JSON.parse(opts.body).run_id||'run-'+ ++next})};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'capture.js'),'utf8'),context);
 await vm.runInContext('monitorStart();monitorQueue',context);
 assert.equal(calls[0].url,'http://monitor/api/start');assert.equal(video.style.cssText.includes('left:-10000px'),true);
 context.master.currentTime=12;await vm.runInContext('captureFrame()',context);
 assert.equal(calls[1].opts.headers['X-Background-On'],'false');assert.equal(closes,0);
 await vm.runInContext('captureFrame()',context);assert.equal(calls.filter(c=>c.url.endsWith('/api/frame')).length,2);assert.equal(closes,0);
 context.playing=false;await vm.runInContext("monitorStop('paused');monitorQueue",context);
 assert(closes>=1);assert.equal(JSON.parse(calls.at(-1).opts.body).kind,'paused');assert.equal(JSON.parse(calls.at(-1).opts.body).skipped_frames,1);
 context.playing=true;await vm.runInContext('monitorStart();monitorQueue',context);
 assert.equal(JSON.parse(calls.at(-1).opts.body).run_id,'run-1');
 context.playing=false;context.master.currentTime=60;await vm.runInContext("monitorStop('completed');monitorQueue",context);
 context.playing=true;context.master.currentTime=0;await vm.runInContext('monitorStart();monitorQueue',context);
 assert.equal(JSON.parse(calls.at(-1).opts.body).run_id,null);
 await vm.runInContext("monitorStop('interrupted');monitorQueue",context);
});

test('Keep camera open and retry unavailable processor until pause',async()=>{
 let clock=0,closes=0,starts=0,ticks=0;const calls=[];
 const video={style:{},readyState:0,play:async()=>{}};
 const context={AbortSignal,Date:{now:()=>clock},monitorConfig:{url:'http://monitor',token:'ticket'},playing:true,master:{currentTime:0},duration:60,voice:{muted:false},background:null,
 document:{createElement:t=>t==='video'?video:{},body:{appendChild(){}},getElementById:()=>({textContent:''})},
 navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){closes++;}}]})}},window:{addEventListener(){}},setInterval:()=>{ticks++;return 1;},clearInterval(){},
 fetch:async(url,opts)=>{calls.push(url);if(url.endsWith('/api/start')&&++starts===1)return {ok:false,status:503};return {ok:true,status:200,json:async()=>({run_id:'same-run'})};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'capture.js'),'utf8'),context);
 await vm.runInContext('monitorStart();monitorQueue',context);
 assert.equal(starts,1);assert.equal(closes,0);assert.equal(ticks,1);
 clock=1100;await vm.runInContext('captureFrame()',context);
 assert.equal(starts,2);assert.equal(closes,0);
 assert.equal(vm.runInContext('monitorRun',context),'same-run');
 context.playing=false;await vm.runInContext("monitorStop('paused');monitorQueue",context);
 assert.equal(closes,1);const before=calls.length;clock=5000;await vm.runInContext('captureFrame()',context);assert.equal(calls.length,before);
});

test('Browser uses authenticated persistent frames and closes the connection on pause',async()=>{
 const packets=[],requests=[];let closed=0;
 class Socket{
  constructor(url){this.url=url;this.readyState=1;queueMicrotask(()=>this.onopen());}
  send(data){if(typeof data==='string'){assert.equal(JSON.parse(data).token,'ticket');queueMicrotask(()=>this.onmessage({data:JSON.stringify({type:'ready'})}));}
   else{packets.push(data);queueMicrotask(()=>this.onmessage({data:JSON.stringify({type:'frame',status:202})}));}}
  close(){this.readyState=3;closed++;if(this.onclose)this.onclose();}
 }
 const video={style:{},readyState:2,videoWidth:640,videoHeight:480,play:async()=>{}};
 const context={AbortSignal,URL,WebSocket:Socket,setTimeout,clearTimeout,monitorConfig:{url:'http://monitor',token:'ticket'},playing:true,master:{currentTime:12.5},duration:60,voice:{muted:false},background:{muted:true},
 document:{createElement:t=>t==='video'?video:{getContext:()=>({drawImage(){},getImageData:()=>({data:new Uint8Array(640*480*4).fill(7)})})},body:{appendChild(){}},getElementById:()=>({textContent:''})},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}},window:{addEventListener(){}},setInterval:()=>1,clearInterval(){},
 fetch:async(url)=>{requests.push(url);return {ok:true,json:async()=>({run_id:'run',frame_socket:'/api/frames'})};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'capture.js'),'utf8'),context);
 await vm.runInContext('monitorStart();monitorQueue',context);await vm.runInContext('captureFrame()',context);
 assert.equal(packets.length,1);const view=new DataView(packets[0]);assert.equal(view.getUint16(0,true),640);assert.equal(view.getUint16(2,true),480);assert.equal(view.getFloat64(4,true),12.5);assert.equal(view.getUint8(12),1);assert.equal(view.getUint8(16),7);
 assert(!requests.some(url=>url.endsWith('/api/frame')));
 context.playing=false;await vm.runInContext("monitorStop('paused');monitorQueue",context);assert.equal(closed,1);assert(requests.at(-1).endsWith('/api/stop'));
});

test('Native capture starts on play, sends only playback heartbeat, and stops on pause',async()=>{
 let cameraRequests=0;const requests=[];
 const context={AbortSignal,monitorConfig:{url:'http://monitor',token:'ticket',capture_mode:'native'},playing:true,master:{currentTime:12},duration:60,voice:{muted:false},background:{muted:true},
 document:{createElement:()=>({style:{}}),body:{appendChild(){}},getElementById:()=>({textContent:''})},navigator:{mediaDevices:{getUserMedia:async()=>{cameraRequests++;throw new Error('Browser camera must not be opened');}}},window:{addEventListener(){}},setInterval:(fn,delay)=>{assert.equal(delay,1000);return 1;},clearInterval(){},
 fetch:async(url,opts)=>{requests.push({url,data:JSON.parse(opts.body)});return {ok:true,json:async()=>({run_id:'native-run',capture_mode:'native'})};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'capture.js'),'utf8'),context);
 await vm.runInContext('monitorStart();monitorQueue',context);assert.equal(cameraRequests,0);
 await vm.runInContext('captureFrame()',context);assert(requests[1].url.endsWith('/api/ping'));assert.equal(requests[1].data.position,12);assert.equal(requests[1].data.levels.background_on,false);
 context.playing=false;await vm.runInContext("monitorStop('paused');monitorQueue",context);assert(requests.at(-1).url.endsWith('/api/stop'));assert.equal(requests.at(-1).data.kind,'paused');
 const count=requests.length;await vm.runInContext('captureFrame()',context);assert.equal(requests.length,count);
});

test('Camera startup has a warm-up deadline and recovered timeouts do not leave a stale error',async()=>{
 let clock=0,starts=0;const timeouts=[];const errorElement={textContent:''};
 const context={AbortSignal:{timeout(ms){timeouts.push(ms);return {};}},Date:{now:()=>clock},monitorConfig:{url:'http://monitor',token:'ticket',capture_mode:'native'},playing:true,master:{currentTime:0},duration:60,voice:{muted:false},background:null,
 document:{createElement:()=>({style:{}}),body:{appendChild(){}},getElementById:()=>errorElement},window:{addEventListener(){}},setInterval:()=>1,clearInterval(){},
 fetch:async(url)=>{if(url.endsWith('/api/start')&&++starts===1)throw Object.assign(new Error('signal timed out'),{name:'TimeoutError'});return {ok:true,json:async()=>({run_id:'run',capture_mode:'native'})};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'capture.js'),'utf8'),context);
 await vm.runInContext('monitorStart();monitorQueue',context);
 assert.equal(timeouts[0],45000);assert(errorElement.textContent.includes('Retrying automatically'));assert(!errorElement.textContent.includes('signal timed out'));
 clock=1100;await vm.runInContext('captureFrame()',context);assert.equal(errorElement.textContent,'');
 errorElement.textContent='An unrelated audio warning';await vm.runInContext('captureFrame()',context);assert.equal(errorElement.textContent,'An unrelated audio warning');
 context.playing=false;await vm.runInContext("monitorStop('paused');monitorQueue",context);assert.equal(timeouts.at(-1),20000);
});
