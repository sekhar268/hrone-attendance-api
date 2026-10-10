"""Idempotently insert deterministic synthetic records into the demo database."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def seed_demo_records(db):
    if db.name != 'attendance_demo':
        raise RuntimeError('Refusing to seed a database other than attendance_demo')
    marker = 'synthetic-v1'
    if db.demo_seed_state.find_one({'_id': marker}):
        return

    ist = ZoneInfo('Asia/Kolkata')
    employees = [
        {'emp_code': 'EMP9001', 'name': 'Asha Demo', 'email': 'asha@example.invalid', 'department': 'Engineering', 'shift_start': '09:30', 'shift_end': '18:30', 'joined_on': '2026-01-05'},
        {'emp_code': 'EMP9002', 'name': 'Ravi Sample', 'email': 'ravi@example.invalid', 'department': 'Engineering', 'shift_start': '09:30', 'shift_end': '18:30', 'joined_on': '2026-02-02'},
        {'emp_code': 'EMP9003', 'name': 'Mina Example', 'email': 'mina@example.invalid', 'department': 'People', 'shift_start': '10:00', 'shift_end': '19:00', 'joined_on': '2026-03-02'},
    ]
    for employee in employees:
        db.employees.update_one({'emp_code': employee['emp_code']}, {'$set': employee}, upsert=True)

    start = date(2026, 10, 1)
    for employee in employees:
        sh, sm = map(int, employee['shift_start'].split(':'))
        eh, em = map(int, employee['shift_end'].split(':'))
        for offset in range(9):
            day = start + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            in_minute = sh * 60 + sm + (18 if employee['emp_code'] == 'EMP9002' and offset in (1, 6) else 0)
            out_minute = eh * 60 + em + (42 if employee['emp_code'] == 'EMP9001' and offset == 3 else 0)
            pin = datetime.combine(day, time(in_minute // 60, in_minute % 60), ist).astimezone(timezone.utc)
            pout = datetime.combine(day, time(out_minute // 60, out_minute % 60), ist).astimezone(timezone.utc)
            record = {
                'emp_code': employee['emp_code'], 'date': day.isoformat(), 'status': 'PRESENT',
                'punch_in': pin, 'punch_out': pout, 'work_hours': round((pout-pin).total_seconds()/3600, 2),
                'late_minutes': (in_minute - (sh*60+sm)) if in_minute > sh*60+sm+10 else 0,
                'overtime_minutes': 42 if employee['emp_code'] == 'EMP9001' and offset == 3 else 0,
                'half_day': False, 'history': [],
            }
            db.attendance_logs.update_one({'emp_code': employee['emp_code'], 'date': day.isoformat()}, {'$set': record}, upsert=True)
    db.demo_seed_state.insert_one({'_id': marker, 'loaded_at': datetime.now(timezone.utc)})


if __name__ == '__main__':
    import os
    from dotenv import load_dotenv
    from pymongo import MongoClient

    load_dotenv()
    if os.getenv('MONGO_DB') != 'attendance_demo':
        raise SystemExit('Refusing to seed: set MONGO_DB=attendance_demo first')
    uri = os.getenv('MONGO_URI')
    if not uri:
        raise SystemExit('Set MONGO_URI to the dedicated demo cluster first')
    seed_demo_records(MongoClient(uri, tz_aware=True)['attendance_demo'])
    print('Seeded synthetic demo records in attendance_demo')
