import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

results_dir = Path(r"F:\Projects\AI-Operator\10x_results")
results_dir.mkdir(exist_ok=True)

services_core = Path(r"F:\Projects\AI-Operator\services\core")

for i in range(1, 11):
    out_file = results_dir / f"run_{i}.txt"
    print(f"=== Run {i}/10 ===", flush=True)
    start_time = datetime.now()
    
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_run_controller.py", "-v", "--tb=short"],
        cwd=services_core,
        capture_output=True,
        text=True
    )
    
    duration = (datetime.now() - start_time).total_seconds()
    out_file.write_text(result.stdout + result.stderr + f"\nEXIT_CODE: {result.returncode}\nDURATION: {duration}s\n")
    
    print(f"Run {i} completed with exit code {result.returncode} in {duration}s", flush=True)
    
    if result.returncode != 0:
        print(f"FAILURE detected in run {i}. Stopping.", flush=True)
        sys.exit(1)

print("All 10 runs completed successfully.", flush=True)
