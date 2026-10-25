"""A single synchronised player with independent client-side track controls."""
import base64
import json
from html import escape
from pathlib import Path


def player_html(session, monitoring=None):
    def audio_tag(key):
        path = session.get(key)
        if not path:
            return ''
        data = base64.b64encode(Path(path).read_bytes()).decode('ascii')
        return f'<audio id="{key}" preload="metadata" src="data:audio/mpeg;base64,{data}"></audio>'

    voice_disabled = '' if session.get('voice') else 'disabled'
    background_disabled = '' if session.get('background') else 'disabled'
    duration = int(session['duration_seconds'])
    monitoring_js = "function monitorStart(){} function monitorStop(){}"
    if monitoring:
        config = json.dumps({'url': monitoring['url'], 'token': monitoring['token'], 'capture_mode': monitoring.get('capture_mode', 'browser')}).replace('<', '\u003c')
        monitoring_js = 'const monitorConfig=' + config + ';\n' + (Path(__file__).parent / 'monitoring' / 'capture.js').read_text()

    return '''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box}body{margin:0;font:15px system-ui,sans-serif;color:#fff;background:transparent}
.player{padding:22px;border:1px solid #ffffff35;border-radius:22px;background:#111820e8}
h3{margin:0 0 18px}button{border:1px solid #ffffff50;border-radius:22px;padding:10px 22px;color:white;background:#354b45;cursor:pointer}
.timeline{display:flex;gap:12px;align-items:center;margin:18px 0}progress{flex:1;width:100%;height:7px;accent-color:#b3d8c7;border-radius:8px}
.layers{display:flex;gap:24px;flex-wrap:wrap}.layer{display:grid;gap:10px;flex:1;min-width:180px}
label{display:flex;gap:12px;align-items:center;font-size:17px}input[type=checkbox]{appearance:none;width:46px;height:26px;border-radius:20px;background:#52615d;position:relative;cursor:pointer}input[type=checkbox]::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:white;transition:transform .2s}input[type=checkbox]:checked{background:#6eaa91}input[type=checkbox]:checked::after{transform:translateX(20px)}input:disabled{opacity:.4}#error{color:#ffbaba;margin-top:12px}
</style></head><body><div class="player"><h3>__TITLE__</h3>
__AUDIO__
<button id="play" type="button">Play session</button>
<div class="timeline"><span id="elapsed">0:00</span><progress id="seek" aria-label="Session progress" max="__DURATION__" value="0"></progress><span id="total"></span></div>
<div class="layers">
<div class="layer"><label><input id="voiceOn" type="checkbox" role="switch" checked __VOICE_DISABLED__>Voice <span id="voiceState">On</span></label></div>
<div class="layer"><label><input id="backgroundOn" type="checkbox" role="switch" checked __BACKGROUND_DISABLED__>Background <span id="backgroundState">On</span></label></div>
</div><div id="error" role="status"></div></div>
<script>
const duration=__DURATION__;
const voice=document.getElementById('voice'), background=document.getElementById('background');
const tracks=[voice,background].filter(Boolean),master=voice||background;
const play=document.getElementById('play'),seek=document.getElementById('seek');
let playing=false;
const format=t=>Math.floor(t/60)+':'+String(Math.floor(t%60)).padStart(2,'0');
document.getElementById('total').textContent=format(duration);
function pause(kind='paused'){tracks.forEach(t=>t.pause());playing=false;play.textContent='Play session';monitorStop(kind);}
async function start(){
  document.getElementById('error').textContent='';
  const position=Number(seek.value)>=duration-.1?0:Number(seek.value);
  tracks.forEach(t=>{t.currentTime=position;});
  try{await Promise.all(tracks.map(t=>t.play()));playing=true;play.textContent='Pause session';monitorStart();}
  catch(e){pause();document.getElementById('error').textContent='Playback could not start. Please press Play again.';}
}
play.onclick=()=>playing?pause():start();
for(const [track,toggle,state,volume] of [[voice,'voiceOn','voiceState',1],[background,'backgroundOn','backgroundState',0.65]]){
 const control=document.getElementById(toggle),label=document.getElementById(state);
 if(!track){control.checked=false;label.textContent='Off';continue;}
 track.volume=volume;
 control.onchange=e=>{track.muted=!e.target.checked;label.textContent=e.target.checked?'On':'Off';};
}
function tick(){
 if(playing&&master){
  const t=Math.min(master.currentTime,duration);
  seek.value=t;document.getElementById('elapsed').textContent=format(t);
  if(t>=duration-.03){pause('completed');seek.value=duration;document.getElementById('elapsed').textContent=format(duration);}
  else for(const track of tracks){if(track!==master&&Math.abs(track.currentTime-t)>.25)track.currentTime=t;}
 }
 requestAnimationFrame(tick);
}
if(master)master.onended=()=>{pause('completed');seek.value=duration;document.getElementById('elapsed').textContent=format(duration);};
window.addEventListener('pagehide',()=>pause('interrupted'));tick();
__MONITORING__
</script></body></html>'''.replace('__MONITORING__', monitoring_js).replace('__TITLE__', escape(session['title'])).replace('__AUDIO__', audio_tag('voice') + audio_tag('background')).replace('__DURATION__', str(duration)).replace('__VOICE_DISABLED__', voice_disabled).replace('__BACKGROUND_DISABLED__', background_disabled)
