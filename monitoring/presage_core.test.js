'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events');
const {createPresageCore}=require('./presage_core');

test('Reference bundles, RGBA input, full decoder payload, bounded histories and error reset',async()=>{
 let sdk,config,resets=0,inputs=0,frame;
 class FakeSDK extends EventEmitter{
  constructor(options){super();sdk=this;config=options;this.processingStatus=1;}
  useCustomInput(){inputs++;}
  reset(){resets++;this.processingStatus=1;}
  start(){this.processingStatus=3;this.emit('processingStatus',3);}
  async stopAsync(){this.processingStatus=1;this.emit('processingStatus',1);}
  sendFrame(...args){frame=args;return true;}
 }
 const delivered=[];
 const core=createPresageCore({SmartSpectraSDK:FakeSDK,PixelFormat:{kRGBA:7},ProcessingStatus:{kUninitialized:0,kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:9},breathingMetrics:[1],cardioMetrics:[2],faceMetrics:[3],decodeMetrics:x=>x},'fake-key',async payload=>{delivered.push(payload);});
 assert.deepEqual(config.requestedMetrics,[1,2,3]);assert.equal(config.enableTelemetry,false);assert.equal(config.apiKey,'fake-key');
 core.startMeasurement();assert.equal(core.state.running,true);
 assert.equal(core.sendFrame(Buffer.alloc(16),2,2,1234),true);assert.equal(frame[3],8);assert.equal(frame[4],7);assert.equal(frame[5],1234);
 const pulse=Array.from({length:70},(_,timestamp)=>({value:70,confidence:90,stable:true,timestamp}));
 sdk.emit('metrics',{cardio:{pulseRate:pulse},breathing:{rate:[]},face:{expression:[{timestamp:1,stable:true,scores:[{type:5,confidence:80}]}],landmarks:[{value:[1,2]}]}},500);
 await new Promise(r=>setImmediate(r));
 assert.equal(delivered[0].type,'metrics');assert.equal(delivered[0].timestampUs,500);assert.equal(delivered[0].metrics.cardio.pulseRate.length,60);assert.equal(core.state.metricHistory.pulse.length,60);assert.equal(core.state.metricHistory.expressions[0].scores[0].type,5);assert.equal(delivered[0].metrics.face.landmarks,undefined);assert.equal(config.enableAccumulatedOutput,true);
 sdk.emit('validationStatus',7,500,'Place more of the chest in view.');assert.equal(core.state.validation.hint,'Place more of the chest in view.');
 sdk.processingStatus=9;await core.stopMeasurement();assert.equal(resets,1);assert.equal(inputs,2);assert.equal(core.state.running,false);
 core.startMeasurement();await core.stopMeasurement();assert.equal(core.state.processingStatus,1);
});

test('App can request only pulse, breathing and expressions without optional pressure models',()=>{
 let config;
 class SDK{constructor(options){config=options;}useCustomInput(){}on(){}}
 createPresageCore({SmartSpectraSDK:SDK,breathingMetrics:[0,1,2,3,4,5,6,7],cardioMetrics:[15,16,17],faceMetrics:[11,12,13,14]},'key',async()=>{},[15,2,14]);
 assert.deepEqual(config.requestedMetrics,[15,2,14]);
 assert(!config.requestedMetrics.includes(16));
 assert(!config.requestedMetrics.includes(17));
});

test('Native camera targets 30fps and is selected again after error reset',async()=>{
 let sdk,cameraCalls=0,optionsSeen;
 class SDK extends EventEmitter{
  constructor(){super();sdk=this;this.processingStatus=1;}
  useCamera(selection,options){assert.equal(selection.kind,'default');optionsSeen=options;cameraCalls++;}
  useCustomInput(){throw new Error('Native mode must not select browser input');}
  start(){this.processingStatus=3;}
  reset(){this.processingStatus=1;}
  async stopAsync(){this.processingStatus=1;}
 }
 const api={SmartSpectraSDK:SDK,CameraSelection:{default:{kind:'default'}},ProcessingStatus:{kUninitialized:0,kIdle:1,kStarting:2,kRunning:3,kStopping:4,kError:5},breathingMetrics:[],cardioMetrics:[],faceMetrics:[]};
 const core=createPresageCore(api,'key',async()=>{},[15,2,14],'native');
 assert.deepEqual(optionsSeen,{width:640,height:480,fps:30});core.startMeasurement();
 sdk.processingStatus=5;await core.stopMeasurement();assert.equal(cameraCalls,2);
 core.startMeasurement();await core.stopMeasurement();assert.equal(cameraCalls,2);
});
