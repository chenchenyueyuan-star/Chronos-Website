"""Account-only controls, service lifecycle, and descriptive results."""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import streamlit as st
from pathlib import Path
from measurement_store import MeasurementStore
from measurement_receiver import start_receiver
from result_report import overview, text_report, readings_csv, gaps, camera_hint

HERE = Path(__file__).resolve().parent


@st.cache_resource
def services():
    store = MeasurementStore(HERE / 'private_data' / 'measurements.sqlite3')
    receiver = start_receiver(store, int(os.getenv('CHRONOS_RUNTIME_RECEIVER_PORT') or os.getenv('MEASUREMENT_RECEIVER_PORT', '8766')))
    return store, receiver


def revoke_player():
    config = st.session_state.pop('measurement_player', None)
    if config:
        try:
            services()[0].revoke(config['token'])
        except Exception:
            pass


@st.fragment(run_every="2s")
def live_camera_feedback():
    config = st.session_state.get('measurement_player')
    if not config:
        return
    request = Request(config['url'] + '/api/status', data=b'{}', method='POST',
                      headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=0.8) as response:
            status = json.load(response)
    except HTTPError as error:
        error.close()
        if error.code == 409:
            st.caption('Camera is paused. Press Play to collect readings.')
        return
    except (OSError, ValueError):
        return
    validation = status.get('validation') or {}
    hint = validation.get('hint')
    if validation.get('code') and hint:
        st.info(hint)
    elif status.get('running'):
        st.caption('Camera is recording. Keep your face and upper chest in view and hold still.')
    fps = status.get('accepted_fps')
    if isinstance(fps, (int, float)) and 0 < fps < 25:
        st.caption('The camera is still below 25 fps. Improve lighting and close other camera apps.')
    receiving = [name for name, present in status.get('metrics_seen', {}).items() if present]
    if receiving:
        st.caption('Readings received: ' + ', '.join(receiving) + '.')


def settings(load_db, save_db):
    if st.session_state.get('guest') or not st.session_state.get('signed_up'):
        return
    identity = st.session_state.get('measurement_identity')
    with st.expander('Session measurements'):
        if identity != st.session_state.get('user_email'):
            st.caption('Sign out and sign in again to enable or view private session measurements.')
            return
        database = load_db()
        user = database.get(identity, {})
        st.caption('Signed-in playback asks for camera permission to measure pulse, breathing, and facial-expression scores. Turn this off to play without measurements. No preview or recording is saved. SmartSpectra processes frames locally; its key/subscription check needs a connection. Results are stored privately on this computer under your account. Camera access stops when you pause or finish. Face the camera in steady light for reliable readings.')
        enabled = st.toggle('Enable measurements during my sessions', value=user.get('measurement_consent') is not False, key='measurement_opt_in_v2')
        if enabled != (user.get('measurement_consent') is not False):
            user['measurement_consent'] = enabled
            database[identity] = user
            save_db(database)
            revoke_player()
        if not enabled:
            st.caption('Camera is off. Enable the switch above before pressing Play.')
        else:
            try:
                with urlopen((os.getenv('CHRONOS_RUNTIME_MONITOR_URL') or os.getenv('MONITOR_BROWSER_URL','http://127.0.0.1:3001')) + '/health', timeout=1) as response:
                    health = json.load(response)
                st.caption('Monitor ready: camera permission will be requested when you press Play.' if health.get('configured') else 'Add PRESAGE_API_KEY to .env.local and restart with ./run.command.')
            except Exception:
                st.caption('Monitor is not running. Restart the app using ./run.command.')
        if enabled:
            live_camera_feedback()
        st.caption('Allow camera access for Terminal or your launching app in macOS settings when asked. You can turn measurements off at any time.' if os.getenv('CHRONOS_RUNTIME_CAMERA_INPUT') == 'native' else 'Allow camera access in your browser when asked. You can turn measurements off at any time.')


def player_config(session, load_db):
    if st.session_state.get('guest') or not st.session_state.get('signed_up'):
        return None
    identity = st.session_state.get('measurement_identity')
    if not identity or identity != st.session_state.get('user_email'):
        return None
    if load_db().get(identity, {}).get('measurement_consent') is False:
        return None
    fingerprint = session.get('folder') or session.get('voice') or session.get('title')
    existing = st.session_state.get('measurement_player')
    if existing and existing['fingerprint'] == fingerprint:
        return existing
    revoke_player()
    store, _ = services()
    metadata = {key: session.get(key) for key in ('title','duration_seconds','folder','database_user_id','database_session_id')}
    metadata['audio_plan'] = session.get('plan', {})
    token = store.issue_ticket(identity, st.session_state.get('username') or identity, metadata)
    config = {'token': token, 'fingerprint': fingerprint,
              'capture_mode': os.getenv('CHRONOS_RUNTIME_CAMERA_INPUT', 'browser'),
              'url': (os.getenv('CHRONOS_RUNTIME_MONITOR_URL') or os.getenv('MONITOR_BROWSER_URL','http://127.0.0.1:3001'))}
    st.session_state['measurement_player'] = config
    return config


@st.fragment
def show_results(in_profile=False):
    if not in_profile:
        st.title('My session results')
    identity = st.session_state.get('measurement_identity')
    if st.session_state.get('guest') or not identity or identity != st.session_state.get('user_email'):
        st.info('Sign in to your account to view private session results.')
        return
    store, _ = services()
    runs = store.runs_for(identity)
    # Clicking refresh reruns only this fragment and reloads the queries above.
    st.button('Refresh results')
    st.caption('These are camera-derived observations during playback, not proof that audio caused a change or a clinical assessment. Early/late comparisons use stable samples with confidence ≥ 50%; missing or low-quality readings are not treated as improvements.')
    if not runs:
        st.info('No measured runs yet. Enable session measurements and play a session with camera permission.')
        return
    lookup = {row['id']:row for row in runs}
    run_id = st.selectbox('Measured run', list(lookup), format_func=lambda key:f"{lookup[key]['started'][:19]} UTC · {json.loads(lookup[key]['metadata']).get('title','Session')} · {lookup[key]['status']}")
    result = store.result(identity, run_id)
    st.markdown('#### Session summary')
    st.table([{'Detail':key, 'Result':value} for key,value in overview(result).items()])
    if result['partial']:
        st.caption(f"Results so far: readings saved through {result['captured_until_seconds']:.0f} seconds. The audio does not need to finish to view or download them.")
    if not result['samples']:
        hint=camera_hint(result)
        st.info('No readings were collected.' + (' Camera feedback: '+hint if hint else ' Check camera access, framing, lighting, and allow time for the sensor to warm up.'))
    missed = gaps(result)
    if any(missed.values()):
        st.caption(f"Skipped: {missed['skipped_measurement_batches']} measurement batches · {missed['skipped_camera_frames']} camera frames. Saved readings are preserved.")
    for metric, unit in [('pulse','beats/min'),('breathing','breaths/min')]:
        summary = result['summary'][metric]
        st.subheader(metric.capitalize())
        st.write(f"{summary['usable_samples']} usable readings out of {summary['samples']} saved readings")
        if 'change' in summary:
            st.table([{'Period':'Early-session average','Value':f"{summary['early_mean']:.1f} {unit}"},{'Period':'Late-session average','Value':f"{summary['late_mean']:.1f} {unit}"},{'Period':'Observed change','Value':f"{summary['change']:+.1f} {unit}"}])
        else:
            st.caption(('No readings were collected for this measurement.' + (' Presage feedback: ' + camera_hint(result) if camera_hint(result) else '')) if not summary['samples'] else 'Readings were saved, but there are not enough stable readings over time for an early/late comparison.')
        rows = [{'Playback seconds':s['playback_seconds'],unit:s['value']} for s in result['samples'] if s['metric']==metric and s['stable'] and s['confidence'] is not None and s['confidence']>=50]
        if rows:
            st.line_chart(rows,x='Playback seconds',y=unit)
    st.subheader('Facial-expression scores')
    st.caption('Model estimates of visible expressions, not a reliable reading of a person’s internal emotions.')
    expressions = [s for s in result['samples'] if s['metric'].startswith('expression:')]
    stable_expressions = [s for s in expressions if s['stable']]
    if stable_expressions:
        rows = [{'Playback seconds':s['playback_seconds'], 'Score (%)':s['value'], 'Expression':s['metric'].split(':',1)[1].capitalize()} for s in stable_expressions]
        st.line_chart(rows, x='Playback seconds', y='Score (%)', color='Expression')
        st.caption(f'{len(stable_expressions)} stable expression scores saved.')
    else:
        st.caption('No stable facial-expression scores were returned for this run. Check camera permission, framing, and expression access on your Presage plan.')
    st.divider()
    st.markdown('#### Save these results')
    st.caption('Choose the readable report for a summary, or CSV to inspect individual readings.')
    st.download_button('Download readable report (.txt)', text_report(result), file_name=f'chronos-report-{run_id}.txt', mime='text/plain')
    st.download_button('Download readings table (.csv)', readings_csv(result), file_name=f'chronos-readings-{run_id}.csv', mime='text/csv')
    export_data = dict(result, run={key:value for key,value in result['run'].items() if key != 'token_hash'})
    st.download_button('Download results so far' if result['partial'] else 'Download my run data',json.dumps(export_data,indent=2),file_name=f'chronos-run-{run_id}.json',mime='application/json')
    st.caption('The export includes playback/mute events, quality flags, and the measured samples. Camera frames are not stored.')
