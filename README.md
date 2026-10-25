# ⏳ Chronos: Rest & Focus

**Built for OxHack | Theme: Time**

Chronos saves you time by maximizing the efficiency of your rest and focus periods. Instead of mindless scrolling or struggling to fall asleep, Chronos uses AI-generated, dynamically mixed audio to actively guide your mind into a state of deep relaxation or hyper-focus in the exact amount of time you have available.

## Current local app

Start the integrated app with `./run.command`. The launcher uses this folder's `.venv` and starts the Presage camera service alongside Streamlit.

- Accounts, introduction answers, session answers, preferences and generated scripts use the configured TigerData database (`TIMESCALE_SERVICE_URL` in `.env.local`). The existing `database_v3` schema and question banks are reused.
- Existing local password accounts migrate to a hashed cloud login only after a successful password check. Keep `users_db.json` until those accounts have signed in. Original Presage result ownership is preserved.
- Profile reports and optional media controls refresh independently of the player. Camera feedback is available inside Profile during playback. Generation shows a loading indicator without provider progress logs.
- Presage capture and reports remain local, with camera access only during authenticated playback. Local run metadata links to the TigerData user/session IDs. Camera frames are not uploaded or saved.
- Generated voice/background tracks can be saved to Snowflake from the existing Profile menu after generation. This requires choosing the tracks, agreeing to storage, and entering the encrypted local RSA key's passphrase. The passphrase is not saved by the app. Guests do not save account/session data or collect Presage measurements.

## 🌟 Core Features

* **⏱️ Time-Dilation Micro-Rests:** Only have 4 minutes between meetings? Input your available time, and our Gemini-powered engine generates a hyper-compressed, perfectly timed meditation script that fits your schedule down to the second.

* **🧠 Research-Backed Dynamic Scripts:** Scripts aren't just generic sleep stories. **Gemini** synthesizes psychology research papers on insomnia and Cognitive Behavioral Therapy (CBT) to generate evidence-based breathing exercises and relaxation narratives.

* **🎛️ Rapid "Sound Fingerprint" Onboarding:** Instead of a boring survey, users experience a rapid A/B audio comparison (rain vs. wind, whispers vs. soft-spoken) and a custom 16-personality style questionnaire to build a unique psychological and auditory profile.

* **🌊 Adaptive Audio & Sound Mixing:** Utilizing **ElevenLabs** for hyper-realistic ASMR and calming voice clones (male and female), combined with **Pydub** to dynamically sound-mix custom nature blends on the fly. The audio is adaptive and non-repeating.

* **🌒 Fade-to-Black UI:** As your scheduled session progresses, the Streamlit UI visually adapts. Bright colors gradually fade to dark mode, text blurs, and distractions disappear until only the audio remains, minimizing screen-time stimulation.

* **📊 Predictive Audio Profiling:** We analyze user onboarding data alongside session feedback using **TigerData** and **Snowflake** to predict exactly which voice types and sound frequencies will relax you the fastest based on your personality type.

* **📅 Smart Schedule Sync:** Chronos integrates with your daily calendar to automatically transition your audio environment throughout the day—from high-focus work audio to evening wind-down meditations.

## 🛠️ Tech Stack

* **Frontend & Routing:** [Streamlit](https://streamlit.io/)

* **AI Script Generation:** [Google Gemini API](https://deepmind.google/technologies/gemini/)

* **Voice Synthesis:** [ElevenLabs](https://elevenlabs.io/)

* **Database & Analytics:** [TigerData](https://tigerdata.com) & Snowflake API

* **Audio Processing:** `pydub`

## 🏆 Sponsor Tracks Targeted

* **Google:** Best use of Gemini API (Dynamic, research-backed script generation).

* **TigerData:** Scalable Postgres (User profiling and session logging).

* **HRT / Snowflake:** Best usage of data for predictions (Audio preference prediction based on user profiles).

## 🚀 Getting Started

### Prerequisites

* Python 3.10+

* API Keys for Gemini and ElevenLabs.

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/yourusername/chronos.git
   cd chronos
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Set up your environment variables:
   Create a `.env` file in the root directory and add your keys:

   ```env
   GEMINI_API_KEY=your_key_here
   ELEVENLABS_API_KEY=your_key_here
   ```

4. Run the application:

   ```bash
   streamlit run app.py
   ```

## 🔮 What's Next?

* **Blockchain Storage:** Implement Solana for highly secure, decentralized storage of daily sleep scores.

* **Dream Environment:** A fully interactive drag-and-drop 3D soundboard for users to build their own spatial audio rooms.

*Created by [Your Team Name] for OxHack*
## Current Streamlit app

Run `python -m streamlit run app.py` from this folder.

Continue as guest, or use the existing local demo account flow. The app reads both questionnaires directly from `data/`: 20 onboarding preferences followed by the current-session check-in. Saved legacy placeholder answers must be completed again. The JSON contains 8 session items even though its metadata says 9; the interface uses the actual items.

Google login is optional and requires `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env` or `.env.local`. Local accounts and cookies are prototype functionality, not production authentication. Guest preferences last for the current browser session; account preferences use `users_db.json`.

The session screen prepares a plan, then Generate my session sends the relevant questionnaire answers to Gemini. ElevenLabs synthesises narration and optional nature/ASMR sound-effect loops. Separate voice/background tracks have synchronised playback, independent on/off switches, and downloads. Generated files are saved under ignored `generated_sessions/` folders. TigerData storage is not connected.

### Personalised audio setup

Install `python -m pip install -r requirements.txt`. Copy `.env.example` to `.env.local`, fill `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, and at least the voice ID matching your selected narrator. Each narrator uses only its matching voice ID; there is no shared fallback. Changing the narrator immediately updates the prepared plan and clears the previous audio. Restart the app after changing keys.

Complete preferences and the session check-in, choose a duration, prepare the plan, then click **Generate my session**. API generation uses your provider credits. Playback, switching tracks on or off do not make API calls. Google receives relevant preferences and session choices; ElevenLabs receives script text, sound descriptions, and selected voice settings. No account name/password is sent in generation requests.

Gemini budgets words for the selected duration. The app measures generated speech and distributes full sections with pauses across that timeline; it refuses overlong narration instead of truncating speech. Backgrounds are generated as 20-second seamless loops and repeated/faded to the full duration. MP3 encoding can add a fraction of a second of padding; the player stops at the requested duration. A voice-only session can have intentional quiet pauses. Narration-off sessions never call text-to-speech; disabled nature/ASMR layers are not generated. With no reviewed exercise evidence cards supplied, scripts use neutral narration rather than prescribed guided exercises.

Audio processing uses the FFmpeg binary provided by `imageio-ffmpeg`; no separate Homebrew FFmpeg install is needed.

Script length targets 90 words per minute for sparse narration and 105 for frequent narration, with a 20% tolerance across the whole script. Paragraph lengths and counts can vary naturally; unequal paragraphs do not cause rejection. Clearly short or overlong scripts are corrected with at most two additional Gemini requests before voice generation begins. Actual speech duration is measured before timeline placement; spoken words are not truncated. One-minute sessions are supported.

When ASMR is enabled, narration uses the selected narrator’s `ELEVENLABS_*_WHISPER_ID`. Otherwise it uses that narrator’s normal `ELEVENLABS_*_ID`. Missing whisper IDs produce a configuration message rather than falling back to a normal or different narrator. Narration disabled still means no text-to-speech request. Surrounding spaces in voice IDs are stripped.

Session check-in uses the same one-question-at-a-time A/B cards as onboarding, with back navigation. After answering, choose 1–30 minutes using the scroll wheel (mouse wheel, touch scrolling, option clicks, or arrow keys; Home/End selects 1/30). Playback has Voice and Background on/off switches and a read-only progress bar; no duration, seek, or volume sliders.

## Project files

- `app.py`: Streamlit interface and questionnaire flow.
- `audio_generation.py`: Gemini scripts and ElevenLabs audio processing.
- `audio_player.py`: synchronised voice/background player.
- `duration_picker.py` and `components/minute_wheel/index.html`: duration wheel.
- `data/`: predefined questionnaire JSON files.
- `tests/`: regression tests; these are development files, not a second application.
- `run.command`, `requirements.txt`, `.env.example`: startup and configuration.
- `.env.local`, `users_db.json` (when created), and `generated_sessions/`: private configuration and user data.

The unused Gradio and original Streamlit prototypes, biometrics experiments, separate Node prototype, duplicate requirements, and legacy media samples have been removed.

## Account-only session measurements

The SmartSpectra camera demo supplied in `New folder (2).zip` is integrated as a local, headless Node bridge under `monitoring/`. It requests pulse and breathing groups with SDK telemetry disabled. Camera frames are processed locally and never written to disk; only decoded pulse/breathing samples and facial-expression scores and playback events are saved. The Presage service is contacted for SDK authentication/subscription gating. Results are observational within-session trends, not proof of causation or clinical efficacy.

### Setup and use

1. Install Node.js 20+ and run `npm install --prefix monitoring` after cloning.
2. Add `PRESAGE_API_KEY` to the private `.env.local` file (the key comes from the Presage developer portal).
3. Start with `./run.command`. This now starts both the loopback monitor bridge and Streamlit, then shuts the bridge down when the app exits.
4. Sign in or create an account. A restored legacy username cookie alone does not grant access to private measurements: use **Sign in again for private measurements** if prompted. Google sign-in tokens are signature/audience-verified before they can grant measurement access.
5. Open **👤 profile → Session measurements** to review or disable camera measurements. The browser will request camera permission on Play. Face the camera in steady lighting. There is no camera preview, separate monitoring tab, live vitals dashboard, microphone capture, or saved video.
6. Play a generated session. Pause/finish closes the camera; resume continues the same run, and a completed replay creates a new run. Playback and voice/background on/off state are recorded. Failures do not block audio and do not count as successful measurements.
7. Afterward, open the **👤 profile** and expand **My saved session results**, refresh, and select a run. Each account sees only its own run list, pulse/breathing charts, sample quality, early/late means when sufficient stable readings exist, and a JSON export. Guests have no monitoring controls, ticket, camera code, or records.

Measurement data is stored locally in ignored `private_data/measurements.sqlite3`, linked to account ID and display name. It is not sent to TigerData or Gemini. Early/late comparisons require at least 30 seconds of usable sample coverage and three readings in each edge window; stable samples with confidence ≥ 50% are used. Low confidence, absent faces, or missing samples show insufficient data, not a positive outcome. No pre-playback baseline or control condition is collected.

The bridge is designed for one active camera measurement per local machine; another playback session cannot take it over. It accepts scoped, expiring per-account tickets, binds only to loopback, and rejects other origins. This is a local prototype, not a hosted multi-user deployment. For a non-default app port, set `CHRONOS_APP_ORIGINS` to its loopback origins; the default app is port 8501, bridge 3001, and private receiver 8766. Refreshing/navigating away ends the player; the bridge also times out missing frames so capture does not run indefinitely after a disconnected browser.

Tests: `python -m unittest discover -s tests` and `npm test --prefix monitoring`. Automated tests use synthetic samples and an injected mock SDK and never access a real camera.

The welcome page always offers guest, sign-in, and sign-up on a new visit; saved username cookies do not skip it. Measurement settings and saved run files appear only inside the existing profile popover. The main page and theme sidebar keep their original layout.

Facial-expression measurement requests the SDK EXPRESSIONS metric and saves timestamped score distributions with stability flags under the account. Profile results include expression-score trends and exports. These describe visible expressions; they are not verified internal emotions. Signed-in playback requests browser camera permission if no measurement preference has been saved. An explicit off setting in Profile → Session measurements is always respected; guests never request camera access. The profile reports whether the bridge is configured/running; camera access is requested on Play.

Transient camera-frame upload or measurement-batch save failures skip that point/batch and continue collecting later readings without stopping playback. Bad sample entries are ignored while valid neighbours are kept. Permission/session expiry still stops collection rather than bypassing authorisation. Skipped counts are included in run completion metadata and shown in profile results. Terminal run metadata gets up to three background retries independently of measurement samples; failed sample batches are not queued for replay. Missing results are never fabricated.

While playback is active, missing/invalid readings, transient upload failures, and SDK processing interruptions do not finalise the run. The camera remains open after permission is granted; the bridge resets and retries the processor, and the browser retries a temporarily unavailable service. Heartbeats keep runs alive even while the SDK produces no readings. Pause/end closes capture. Browser permission denial/revocation, session authorisation expiry, and leaving the player cannot be bypassed. Tests inject processor errors and service outages and verify later frames are still accepted.

The launcher selects free loopback ports and passes the exact bridge/receiver URLs to its Streamlit process, avoiding old monitoring services left running by earlier sessions. Open the Local URL printed by the new launcher rather than an older browser tab.

Pausing flushes pending readings and saves a partial result; the session does not have to finish. Open Profile → My saved session results and refresh to view or download results so far. Pause metadata saves also retry in the background; resuming cancels or waits for an older pending pause write so it cannot incorrectly pause the new segment.

Saved results use a session-summary table (account, date, status, requested length and recorded time), separate pulse/breathing comparisons with units, and expression-score charts. Profile downloads include a readable text report, a labelled CSV of individual readings, and the detailed JSON export. Paused reports are explicitly labelled partial; exported JSON omits the internal ticket hash.

## Presage reference alignment

`monitoring/presage_core.js` reuses the supplied `New folder (2)/server.js` SDK logic. Its sample helpers, stop/reset lifecycle, and processing/validation/metrics/error handlers are copied verbatim; `monitoring/reference_manifest.json` records their source hashes, verified by a regression test. The API key is injected from `PRESAGE_API_KEY`, and decoded payload delivery goes through the authenticated storage adapter.

The SDK now requests the reference's exact `[...breathingMetrics, ...cardioMetrics, ...faceMetrics]` bundles, disables telemetry, uses custom RGBA frames with width×4 stride and monotonic microsecond timestamps, decodes the full metrics payload, trims arrays/histories to 60 samples, and follows the same starting/running guards and 10-second stop-to-idle/error-reset sequence. Browser capture uses the same 640×480 / ideal-30-fps request, aspect-preserving resize, one-frame-in-flight rule, and `1000 / 30` timer; pause waits for the pending frame before stopping the SDK.

Application adapters intentionally remain: the camera is invisible on the main page, playback replaces the demo's Start/Stop buttons, guests are excluded, results save under accounts and are accessed in Profile, and transient failures retry as requested rather than permanently stopping the demo. The reference's standalone dashboard is not added to the UI. SDK validation requirements (including visible upper chest for breathing) still apply.

Model delivery recovery: non-fatal SDK error notifications do not reset a running or loading graph. Only an actual SDK error state can schedule recovery, with a 60-second startup grace period and bounded backoff; explicitly non-retryable errors are retained for diagnosis. Native error code, message, retryability, and recovery count are saved with run events. A listener adapter preserves reference handlers because the vendor SDK replaces callbacks registered with the same event name. Pausing/ending can cancel a still-pending model download; no cancelled model is represented as a successful measurement.
