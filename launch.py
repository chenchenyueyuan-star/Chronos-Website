"""Start the local camera bridge alongside Streamlit and stop it on exit."""
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def free_port(preferred):
    for candidate in range(preferred, preferred + 50):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', candidate))
                return candidate
            except OSError:
                continue
    raise RuntimeError('No local port available for this app.')


def main():
    load_dotenv(ROOT / '.env')
    load_dotenv(ROOT / '.env.local', override=True)
    camera_input = os.getenv('CHRONOS_CAMERA_INPUT', 'native' if sys.platform == 'darwin' else 'browser')
    os.environ['CHRONOS_CAMERA_INPUT'] = camera_input
    os.environ['CHRONOS_RUNTIME_CAMERA_INPUT'] = camera_input
    bridge = None
    arguments = list(sys.argv[1:])
    if not any(arg.startswith('--server.port') for arg in arguments):
        app_port = free_port(int(os.getenv('STREAMLIT_SERVER_PORT', '8501')))
        arguments += ['--server.port', str(app_port)]
    else:
        port_arg = next(arg for arg in arguments if arg.startswith('--server.port'))
        app_port = int(port_arg.split('=',1)[1] if '=' in port_arg else arguments[arguments.index(port_arg)+1])
    monitor_port = free_port(int(os.getenv('MONITOR_PORT', '3001')))
    receiver_port = free_port(int(os.getenv('MEASUREMENT_RECEIVER_PORT', '8766')))
    os.environ['MONITOR_PORT'] = str(monitor_port)
    os.environ['MEASUREMENT_RECEIVER_URL'] = f'http://127.0.0.1:{receiver_port}'
    os.environ['CHRONOS_RUNTIME_RECEIVER_PORT'] = str(receiver_port)
    os.environ['CHRONOS_RUNTIME_MONITOR_URL'] = f'http://127.0.0.1:{monitor_port}'
    os.environ['CHRONOS_APP_ORIGINS'] = f'http://localhost:{app_port},http://127.0.0.1:{app_port}'
    try:
        node = shutil.which('node')
        if node and (ROOT / 'monitoring' / 'node_modules' / '@smartspectra' / 'node-sdk').exists():
            bridge = subprocess.Popen([node, 'server.js'], cwd=ROOT / 'monitoring', env=os.environ.copy())
        else:
            print('Optional camera monitoring unavailable. Install Node.js 20+ and run npm install --prefix monitoring. Audio still works.', flush=True)
        app = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'app.py'), *arguments], cwd=ROOT)
        try:
            return app.wait()
        except KeyboardInterrupt:
            app.terminate()
            return app.wait()
    finally:
        if bridge and bridge.poll() is None:
            bridge.terminate()
            try:
                bridge.wait(timeout=12)
            except subprocess.TimeoutExpired:
                bridge.kill(); bridge.wait()


if __name__ == '__main__':
    raise SystemExit(main())
