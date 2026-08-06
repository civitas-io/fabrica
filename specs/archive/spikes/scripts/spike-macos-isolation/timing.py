"""srt latency measurement -- n=10 fresh subprocess calls."""
import subprocess, time

times = []
for i in range(10):
    t0 = time.perf_counter()
    subprocess.run(["srt", "echo", "hello"], capture_output=True, text=True)
    times.append(time.perf_counter() - t0)

ms = sorted(t * 1000 for t in times)
n = len(ms)
print(f"n={n}  min={ms[0]:.1f}ms  p50={ms[n//2]:.1f}ms  max={ms[-1]:.1f}ms")
print("all:", [round(t, 1) for t in ms])
