'use strict';
// SmartSpectra.on replaces a listener. Preserve reference handlers when the
// account adapter also observes validation/errors, without editing the SDK.
function withObservers(SDK){
 return class extends SDK{
  on(event,callback){
   if(!this.chronosObservers)this.chronosObservers=new Map();
   let listeners=this.chronosObservers.get(event);
   if(!listeners){listeners=[];this.chronosObservers.set(event,listeners);super.on(event,(...args)=>{for(const listener of [...listeners])listener(...args);});}
   listeners.push(callback);return this;
  }
 };
}
module.exports={withObservers};
