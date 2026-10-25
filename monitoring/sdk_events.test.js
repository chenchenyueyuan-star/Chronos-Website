'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {withObservers}=require('./sdk_events');
test('Account diagnostics do not replace the reference SDK handlers',()=>{
 class NativeStyleSDK{constructor(){this.listeners=new Map();}on(event,callback){this.listeners.set(event,callback);return this;}emit(event,...args){this.listeners.get(event)?.(...args);}}
 const Wrapped=withObservers(NativeStyleSDK),sdk=new Wrapped(),calls=[];
 sdk.on('validationStatus',code=>calls.push('reference:'+code));
 sdk.on('validationStatus',code=>calls.push('account:'+code));
 sdk.emit('validationStatus',7);
 assert.deepEqual(calls,['reference:7','account:7']);
});
