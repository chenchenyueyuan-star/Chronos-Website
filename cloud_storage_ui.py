"""Optional generated-audio storage, shown only inside the existing profile."""
from pathlib import Path
import streamlit as st
import chronos_snowflake_live as media


@st.fragment
def show_audio_storage(account, session, root):
    if not session or session.get('database_user_id') != account.user_id:
        return
    session_id = session.get('database_session_id')
    if not session_id:
        return
    with st.expander('Save generated audio to Snowflake'):
        tracks = {name: value for name in ('voice', 'background')
                  if (value := session.get(name)) and Path(value).is_file()}
        if not tracks:
            st.caption('No generated audio tracks are available to save.')
            return
        with st.form('snowflake_audio_storage', clear_on_submit=True):
            selected = st.multiselect('Tracks to store', list(tracks), default=list(tracks))
            consent = st.checkbox('I agree to store these generated audio files for this session.')
            passphrase = st.text_input('Snowflake RSA key passphrase', type='password')
            submitted = st.form_submit_button('Save selected audio')
        if submitted:
            if not consent or not selected or not passphrase:
                st.warning('Select a track, tick the checkbox, and enter the key passphrase.')
                return
            try:
                media.consent_to_generated_audio_storage(account.user_id, session_id)
                with st.spinner('Saving your selected audio…'):
                    for track in selected:
                        media.save_generated_audio(account.user_id, session_id, tracks[track], passphrase,
                                                   generated_dir=Path(root) / 'generated_sessions')
                st.success('Selected audio saved to Snowflake.')
            except Exception:
                st.error('The audio could not be saved to Snowflake. Check the connection and key passphrase, then retry.')
