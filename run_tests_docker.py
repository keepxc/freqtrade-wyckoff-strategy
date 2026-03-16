#!/usr/bin/env python3
"""
Docker 环境测试运行脚本
"""
import sys
import subprocess

# 尝试导入 pytest
try:
    import pytest
    print("✅ pytest found")
except ImportError:
    print("❌ pytest not found, installing...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pytest", "--user"])
    import pytest

# 运行测试
print("\n" + "=" * 60)
print("Running Unit Tests in Docker Environment")
print("=" * 60 + "\n")

test_files = [
    "tests/test_daily_event_detector.py",
    "tests/test_event_sequence_state_machine.py",
    "tests/test_market_env_detector.py",
    "tests/test_composite_man_detector.py",
]

for test_file in test_files:
    print(f"\n📋 Running {test_file}...")
    print("-" * 60)
    
    result = pytest.main([test_file, "-v", "--tb=short"])
    
    if result == 0:
        print(f"✅ {test_file} passed")
    else:
        print(f"❌ {test_file} failed with code {result}")

print("\n" + "=" * 60)
print("Test run completed")
print("=" * 60)
