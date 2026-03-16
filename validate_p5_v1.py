#!/usr/bin/env python3
"""Validate P5 V1 implementation completeness."""
import sqlite3
from pathlib import Path


def check_file_exists(path: str, description: str) -> bool:
    """Check if a file exists."""
    exists = Path(path).exists()
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {path}")
    return exists


def check_db_tables(db_path: str) -> bool:
    """Check if all required tables exist in database."""
    required_tables = [
        'experiment_runs',
        'trades',
        'signals',
        'detector_events',
        'regime_snapshots',
        'parameter_versions',
        'weekly_reports',
        'proposals',
    ]
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        all_exist = True
        for table in required_tables:
            exists = table in existing_tables
            status = "✅" if exists else "❌"
            print(f"{status} Table: {table}")
            if not exists:
                all_exist = False
        
        return all_exist
    except Exception as e:
        print(f"❌ Database check failed: {e}")
        return False


def check_sample_data(db_path: str) -> bool:
    """Check if sample data was ingested."""
    try:
        conn = sqlite3.connect(db_path)
        
        runs = conn.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0]
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        detectors = conn.execute("SELECT COUNT(*) FROM detector_events").fetchone()[0]
        regimes = conn.execute("SELECT COUNT(*) FROM regime_snapshots").fetchone()[0]
        
        conn.close()
        
        print(f"\n📊 Sample Data Counts:")
        print(f"  - Runs: {runs}")
        print(f"  - Trades: {trades}")
        print(f"  - Signals: {signals}")
        print(f"  - Detector Events: {detectors}")
        print(f"  - Regime Snapshots: {regimes}")
        
        return runs > 0 and trades > 0 and signals > 0
    except Exception as e:
        print(f"❌ Sample data check failed: {e}")
        return False


def main():
    print("=" * 60)
    print("P5 V1 Implementation Validation")
    print("=" * 60)
    
    print("\n📁 Core Files:")
    files_ok = all([
        check_file_exists("user_data/market.db", "Database"),
        check_file_exists("user_data/event_emitter.py", "Event Emitter"),
        check_file_exists("user_data/ingest/ingest_jsonl.py", "Ingest Script"),
        check_file_exists("user_data/reports/build_weekly_report.py", "Weekly Report Script"),
        check_file_exists("user_data/examples/sample_events.jsonl", "Sample Events"),
        check_file_exists("user_data/events/sample/README.md", "Quick Start Guide"),
    ])
    
    print("\n🗄️  Database Tables:")
    tables_ok = check_db_tables("user_data/market.db")
    
    print("\n📦 Sample Data:")
    data_ok = check_sample_data("user_data/market.db")
    
    print("\n" + "=" * 60)
    if files_ok and tables_ok and data_ok:
        print("✅ P5 V1 Implementation: COMPLETE")
        print("\n📋 Next Steps:")
        print("  1. Integrate EventEmitter into WyckoffV7_TrueMethod strategy")
        print("  2. Run backtest to generate real events")
        print("  3. Ingest events and generate weekly report")
        print("  4. Review data quality and adjust as needed")
    else:
        print("❌ P5 V1 Implementation: INCOMPLETE")
        print("\n⚠️  Please address the missing components above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
