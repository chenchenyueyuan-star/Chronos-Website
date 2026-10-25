'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {mergeMetrics}=require('./metric_batches');
test('Pending batches retain vitals when the next packet contains only expressions',()=>{
 const one=mergeMetrics(null,{cardio:{pulseRate:[{timestamp:1,value:72}]},breathing:{rate:[{value:14}]}},10);
 const two=mergeMetrics(one,{face:{expression:[{timestamp:2,scores:[]}]},cardio:{pulseRate:[{timestamp:1,value:73}]}},20);
 assert.equal(two.cardio.pulseRate.length,1);assert.equal(two.cardio.pulseRate[0].value,73);
 assert.deepEqual(two.breathing.rate,[{timestamp:10,value:14}]);assert.equal(two.face.expression.length,1);
});
test('Pending storage memory is bounded',()=>{
 const merged=mergeMetrics(null,{cardio:{pulseRate:Array.from({length:900},(_,timestamp)=>({timestamp,value:72}))}},0);
 assert.equal(merged.cardio.pulseRate.length,600);
});
