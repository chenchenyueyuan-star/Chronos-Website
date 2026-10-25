"""Readable, consistently labelled session results and tabular exports."""
import csv
import io
import json
from datetime import datetime


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def gaps(result):
    details = [json.loads(e['detail']) for e in result['events'] if e['kind'] in ('paused','completed','failed','interrupted')]
    return {key:max((d.get(key,0) for d in details),default=0) for key in ('skipped_measurement_batches','skipped_camera_frames')}


def camera_hint(result):
    fallback = None
    for event in reversed(result['events']):
        detail = json.loads(event['detail'])
        validations = [detail.get('validation') or {}] + list(reversed(detail.get('validation_history') or []))
        for validation in validations:
            hint = validation.get('hint')
            if isinstance(hint, str) and hint.strip():
                if validation.get('code', 1) != 0:
                    return hint.strip()
                fallback = fallback or hint.strip()
    return fallback


def overview(result):
    run = result['run']
    metadata = json.loads(run['metadata'])
    started = datetime.fromisoformat(run['started']).strftime('%d %b %Y, %H:%M UTC')
    return {
        'Session':metadata.get('title') or 'Session',
        'Account':run['name'],
        'Started':started,
        'Status':run['status'].replace('_',' ').capitalize() + (' · partial results' if result['partial'] else ''),
        'Requested length':clock(metadata.get('duration_seconds',0)),
        'Results saved through':clock(result['captured_until_seconds']),
        'Saved readings':str(len(result['samples'])),
    }


def text_report(result):
    lines=['CHRONOS — SESSION RESULTS','='*40]
    lines += [f'{label}: {value}' for label,value in overview(result).items()]
    hint=camera_hint(result)
    if not result['samples']:
        lines += ['', 'No readings were collected.']
        if hint:lines.append('Camera feedback: '+hint)
    lines += ['', 'PULSE AND BREATHING','-'*40]
    for metric,unit in [('pulse','beats/min'),('breathing','breaths/min')]:
        summary = result['summary'][metric]
        lines += [metric.upper(),f"Saved readings: {summary['samples']}",f"Usable readings: {summary['usable_samples']}"]
        if 'change' in summary:
            lines += [f"Early-session average: {summary['early_mean']:.1f} {unit}",f"Late-session average: {summary['late_mean']:.1f} {unit}",f"Observed change: {summary['change']:+.1f} {unit}"]
        else:
            lines += ['Early/late comparison: not enough reliable data.']
        lines.append('')
    lines += ['FACIAL-EXPRESSION SCORES','-'*40]
    expressions=[s for s in result['samples'] if s['metric'].startswith('expression:') and s['stable']]
    if expressions:
        for name in sorted({s['metric'] for s in expressions}):
            values=[s['value'] for s in expressions if s['metric']==name]
            lines.append(f"{name.split(':',1)[1].capitalize()}: average score {sum(values)/len(values):.1f}% ({len(values)} stable readings)")
    else:
        lines.append('No stable expression scores saved.')
    missed=gaps(result)
    lines += ['', 'DATA QUALITY','-'*40,f"Skipped measurement batches: {missed['skipped_measurement_batches']}",f"Skipped camera frames: {missed['skipped_camera_frames']}",
              'Usable vital readings are stable with confidence of at least 50%.',
              'Early/late comparisons require at least 30 seconds of coverage and 3 readings in each edge window.',
              '', 'These are observations during playback, not proof that the audio caused a change.',
              'Expression scores describe visible expressions, not confirmed internal emotions.']
    return '\n'.join(lines)+'\n'


def readings_csv(result):
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(['playback_time_mm_ss','playback_seconds','measurement','value','unit','confidence_pct','stable','voice_on','background_on'])
    for s in result['samples']:
        unit='%' if s['metric'].startswith('expression:') else 'beats/min' if s['metric']=='pulse' else 'breaths/min'
        writer.writerow([clock(s['playback_seconds']),s['playback_seconds'],s['metric'],s['value'],unit,s['confidence'],bool(s['stable']),bool(s['voice_on']),bool(s['background_on'])])
    return output.getvalue()
