import sys
import subprocess
import time
import os

def run():
    # Detect the virtualenv python
    venv_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python")
    if not os.path.exists(venv_py):
        # Fallback to system python
        venv_py = sys.executable

    print("=" * 60)
    print("STARTING NATDEMY ATTENDANCE SYSTEM")
    print("=" * 60)

    processes = []
    try:
        # 1. Start the Live Monitor Device worker
        print("--> Starting live device monitoring worker...")
        monitor_process = subprocess.Popen(
            [venv_py, "manage.py", "monitor_device"],
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        processes.append(monitor_process)

        # Give it a second to start
        time.sleep(1)

        # 2. Start the Django Web Server
        print("--> Starting Django development web server...")
        web_process = subprocess.Popen(
            [venv_py, "manage.py", "runserver", "0.0.0.0:8000"],
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        processes.append(web_process)

        print("\n" + "=" * 60)
        print("SYSTEM RUNNING SUCCESSFULLY")
        print("Dashboard URL: http://localhost:8000/")
        print("Press Ctrl+C to stop both servers.")
        print("=" * 60 + "\n")

        # Keep parent script alive and monitor subprocesses
        while True:
            # Check if any child died
            for p in processes:
                if p.poll() is not None:
                    raise Exception(f"Process {p.pid} terminated unexpectedly.")
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping all processes...")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait()
        print("Clean exit complete.")

if __name__ == "__main__":
    run()
