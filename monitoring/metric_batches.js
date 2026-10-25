'use strict';
// Bound memory during slow storage without overwriting pending vital samples.
function mergeMetrics(previous, next, timestampUs){
 const result={cardio:{},breathing:{},face:{}};
 for(const [group,field] of [['cardio','pulseRate'],['breathing','rate'],['face','expression']]){
  const samples=new Map();
  for(const sample of previous?.[group]?.[field]||[])samples.set(String(sample.timestamp),sample);
  for(const sample of next?.[group]?.[field]||[]){
   if(!sample||typeof sample!=='object')continue;
   const timestamp=sample.timestamp??timestampUs;
   if(timestamp===undefined||timestamp===null)continue;
   samples.set(String(timestamp),{...sample,timestamp});
  }
  result[group][field]=[...samples.values()].slice(-600);
 }
 return result;
}
module.exports={mergeMetrics};
