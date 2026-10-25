"""Account-only controls, service lifecycle, and descriptive results."""
import json
import os
from urllib.request import urlopen
import altair as alt
import pandas as pd
import streamlit as st
from pathlib import Path
from measurement_store import MeasurementStore
from measurement_receiver import start_receiver
from result_report import overview, text_report, readings_csv, gaps, camera_hint

HERE = Path(__file__).resolve().parent


def response_chart_rows(samples):
    names = {'pulse': 'Heart rate (beats/min)', 'breathing': 'Breathing (breaths/min)'}
    return [
        {
            'Playback seconds': sample['playback_seconds'],
            'Reading': sample['value'],
            'Measurement': names[sample['metric']],
        }
        for sample in samples
        if sample['metric'] in names and sample['stable']
        and sample['confidence'] is not None and sample['confidence'] >= 50
    ]


def build_response_chart(rows):
    return (
        alt.Chart(pd.DataFrame(rows))
        .mark_line(point=True)
        .encode(
            x=alt.X('Playback seconds:Q', title='Playback seconds'),
            y=alt.Y('Reading:Q', title=None, scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip('Measurement:N'),
                alt.Tooltip('Playback seconds:Q', format='.0f'),
                alt.Tooltip('Reading:Q', format='.1f'),
            ],
        )
        .facet(row=alt.Row('Measurement:N', title=None, header=alt.Header(labelAngle=0)))
        .resolve_scale(y='independent')
        .properties(title='Heart rate and breathing during playback')
    )


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
    metadata = {key: session.get(key) for key in ('title','duration_seconds','folder')}
    metadata['audio_plan'] = session.get('plan', {})
    token = store.issue_ticket(identity, st.session_state.get('username') or identity, metadata)
    config = {'token': token, 'fingerprint': fingerprint,
              'capture_mode': os.getenv('CHRONOS_RUNTIME_CAMERA_INPUT', 'browser'),
              'url': (os.getenv('CHRONOS_RUNTIME_MONITOR_URL') or os.getenv('MONITOR_BROWSER_URL','http://127.0.0.1:3001'))}
    st.session_state['measurement_player'] = config
    return config


def show_results(in_profile=False):
    if not in_profile:
        st.title('My session results')
    identity = st.session_state.get('measurement_identity')
    if st.session_state.get('guest') or not identity or identity != st.session_state.get('user_email'):
        st.info('Sign in to your account to view private session results.')
        return
    store, _ = services()
    runs = store.runs_for(identity)
    feedback_history = store.feedback_for_user(identity)
    feedback_by_run = {entry['run_id']: entry for entry in feedback_history}
    if st.button('Refresh results'):
        st.rerun()
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
    st.subheader('Physiological response pattern')
    st.caption('This descriptive view shows stable pulse and breathing readings on separate scales. It is not a stress level, diagnosis, or proof that the audio caused a change.')
    response_rows = response_chart_rows(result['samples'])
    if response_rows:
        response_chart = build_response_chart(response_rows)
        st.altair_chart(response_chart)
        change_rows = [
            {
                'Measurement': label,
                'Early-session average': f"{result['summary'][metric]['early_mean']:.1f} {unit}",
                'Late-session average': f"{result['summary'][metric]['late_mean']:.1f} {unit}",
                'Observed change': f"{result['summary'][metric]['change']:+.1f} {unit}",
            }
            for metric, label, unit in (
                ('pulse', 'Heart rate', 'beats/min'),
                ('breathing', 'Breathing rate', 'breaths/min'),
            )
            if 'change' in result['summary'][metric]
        ]
        if change_rows:
            st.markdown('**Early-to-late comparison**')
            st.table(change_rows)
        else:
            st.caption('A comparison needs at least 30 seconds of usable readings and three readings in each edge window.')
    else:
        st.info('There are not enough stable, high-confidence pulse or breathing readings to show this graph.')
    for metric, unit in [('pulse','beats/min'),('breathing','breaths/min')]:
        summary = result['summary'][metric]
        st.subheader(metric.capitalize())
        st.write(f"{summary['usable_samples']} usable readings out of {summary['samples']} saved readings")
        if 'change' in summary:
            st.table([{'Period':'Early-session average','Value':f"{summary['early_mean']:.1f} {unit}"},{'Period':'Late-session average','Value':f"{summary['late_mean']:.1f} {unit}"},{'Period':'Observed change','Value':f"{summary['change']:+.1f} {unit}"}])
        else:
            st.caption('No readings were collected for this measurement.' if not summary['samples'] else 'Readings were saved, but there are not enough stable readings over time for an early/late comparison.')
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
    st.markdown('#### Session feedback and future suggestions')
    current_feedback = feedback_by_run.get(run_id)
    if result['run']['status'] == 'recording':
        st.caption('Feedback is available after playback ends or is paused.')
    else:
        with st.form(f'session_feedback_{run_id}'):
            rating = st.radio(
                'Was this session a good fit for you?',
                ['Like', 'Dislike'],
                index=(['like', 'dislike'].index(current_feedback['rating']) if current_feedback else None),
                horizontal=True,
                key=f'session_feedback_rating_{run_id}',
            )
            note = st.text_area(
                'Optional note about what you liked or would change',
                value=current_feedback['note'] if current_feedback else '',
                max_chars=500,
                key=f'session_feedback_note_{run_id}',
            )
            submitted = st.form_submit_button('Save feedback')
        if submitted:
            if rating is None:
                st.warning('Choose Like or Dislike before saving feedback.')
            else:
                store.save_feedback(identity, run_id, rating.lower(), note)
                current_feedback = {
                    'rating': rating.lower(),
                    'note': note,
                    'metadata': result['run']['metadata'],
                }
                st.success('Your feedback was saved for this account.')
        if current_feedback and current_feedback['rating'] == 'like':
            liked_plan = json.loads(current_feedback['metadata']).get('audio_plan', {})
            preferences = liked_plan.get('onboarding_preferences', {}) if isinstance(liked_plan, dict) else {}
            asmr_preferences = [
                value.replace('_', ' ')
                for field, value in preferences.items()
                if field.startswith('asmr_') and isinstance(value, str)
            ] if isinstance(preferences, dict) else []
            observed_changes = [
                f"{label.lower()} {result['summary'][metric]['change']:+.1f} {unit}"
                for metric, label, unit in (
                    ('pulse', 'Heart rate', 'beats/min'),
                    ('breathing', 'Breathing rate', 'breaths/min'),
                )
                if 'change' in result['summary'][metric]
            ]
            suggestion = 'You rated this session positively; consider exploring the same declared ASMR preferences'
            if asmr_preferences:
                suggestion += ': ' + ', '.join(asmr_preferences)
            suggestion += '.'
            response_context = (
                'Recorded early-to-late changes: ' + ', '.join(observed_changes) + '.'
                if observed_changes else 'There is not enough data for an early-to-late vital-sign comparison.'
            )
            st.info(suggestion + ' ' + response_context)
            st.caption('This pairs your explicit rating with descriptive measurements only. A whole-session rating cannot identify which sound you preferred, and vital signs do not measure stress or prove the audio caused a change.')
        elif current_feedback and current_feedback['rating'] == 'dislike':
            st.caption('This rating applies to the whole session, so it cannot identify which individual sound you would prefer to change.')
    st.divider()
    st.markdown('#### Save these results')
    st.caption('Choose the readable report for a summary, or CSV to inspect individual readings.')
    st.download_button('Download readable report (.txt)', text_report(result), file_name=f'chronos-report-{run_id}.txt', mime='text/plain')
    st.download_button('Download readings table (.csv)', readings_csv(result), file_name=f'chronos-readings-{run_id}.csv', mime='text/csv')
    export_data = dict(result, run={key:value for key,value in result['run'].items() if key != 'token_hash'})
    st.download_button('Download results so far' if result['partial'] else 'Download my run data',json.dumps(export_data,indent=2),file_name=f'chronos-run-{run_id}.json',mime='application/json')
    st.caption('The export includes playback/mute events, quality flags, and the measured samples. Camera frames are not stored.')
